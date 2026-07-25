"""MCP OAuth 2.1 Authorization Module for ZeroShield Gateway.

Implements the MCP OAuth 2.1 flow so VS Code (and other MCP clients) can
auto-discover and authenticate when adding the gateway as an MCP server
via Cmd+Shift+P → "Add MCP Server" → HTTP → enter URL.

Spec compliance:
- RFC 9728: OAuth 2.0 Protected Resource Metadata (REQUIRED by MCP 2025-06-18)
- RFC 8414: OAuth 2.0 Authorization Server Metadata
- RFC 7591: OAuth 2.0 Dynamic Client Registration Protocol
- RFC 8707: Resource Indicators for OAuth 2.0

Flow:
1. MCP client connects → gateway returns 401 with WWW-Authenticate: Bearer
   resource_metadata="<url>"
2. Client GETs /.well-known/oauth-protected-resource → gets resource metadata
   including authorization_servers list
3. Client GETs /.well-known/oauth-authorization-server → gets OAuth metadata
4. Client POSTs /oauth/register → gets dynamic client_id
5. Client generates PKCE, opens browser to /oauth/authorize
6. User enters their Gateway API Key on the auth page
7. Gateway validates key, redirects to callback with auth code
8. Client POSTs /oauth/token with code + code_verifier → gets access_token
9. Client reconnects with Authorization: Bearer <access_token>

The access_token IS the gateway API key, so the existing AuthMiddleware
validates it seamlessly — zero changes to auth logic needed.
"""

import base64
import hashlib
import html
import logging
import os
import secrets
import time
import uuid
from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

LOG = logging.getLogger("gateway.mcp_oauth")

router = APIRouter(tags=["MCP OAuth"])

_gateway_public_url = (
    (os.environ.get("GATEWAY_PUBLIC_URL", "") or "").strip().rstrip("/")
)


def _base_url(request: Request) -> str:
    """Determine the base URL for OAuth endpoints."""
    if _gateway_public_url:
        return _gateway_public_url
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get(
        "x-forwarded-host", request.headers.get("host", "127.0.0.1:8300")
    )
    return f"{scheme}://{host}"


# ── In-memory stores (suitable for single-instance dev/staging) ──────
# Production should use Redis with TTL.
_registered_clients: dict[str, dict] = {}  # client_id → registration data
_auth_codes: dict[str, dict] = {}  # code → {api_key, client_id, ...}
_AUTH_CODE_TTL = 300  # 5 minutes


def _cleanup_expired():
    """Remove expired auth codes."""
    now = time.time()
    expired = [
        k for k, v in _auth_codes.items() if now - v["created_at"] > _AUTH_CODE_TTL
    ]
    for k in expired:
        del _auth_codes[k]


def _validate_redirect_uri(uri: str) -> bool:
    """Validate redirect URI per MCP spec: localhost or HTTPS only."""
    if not uri:
        return False
    parsed = urlparse(uri)
    if parsed.hostname in ("127.0.0.1", "localhost", "::1", "[::1]"):
        return True
    if parsed.scheme == "https":
        return True
    # VS Code may use vscode:// scheme
    if parsed.scheme in ("vscode", "vscode-insiders", "cursor"):
        return True
    return False


def _redirect_uri_allowed_for_client(client_id: str, redirect_uri: str) -> bool:
    """AU2-01: bind ``redirect_uri`` to the DYNAMICALLY-REGISTERED client's declared
    allowlist. ``_validate_redirect_uri`` only enforces the SCHEME (any HTTPS host passes),
    so without this a rogue/guessed ``client_id`` could redirect the authorization code —
    which IS the user's Gateway API key (see the token endpoint) — to an attacker-controlled
    HTTPS host: full API-key theft. The DCR store (``_registered_clients``) recorded each
    client's ``redirect_uris`` but never read them.

    OAuth 2.1: an EXACT match against the client's registered redirect_uris is required (no
    substring/prefix). An UNREGISTERED ``client_id`` is rejected so an attacker cannot skip
    registration to dodge the binding, and a client that registered without any redirect_uris
    can authorize none. NOTE: ``_registered_clients`` is in-memory (like ``_auth_codes``), so
    a gateway restart drops registrations and the client must re-register (DCR) — acceptable
    for the short-lived in-memory flow; a persistent client store is the follow-up."""
    reg = _registered_clients.get(client_id)
    if not reg:
        return False
    allowed = reg.get("redirect_uris") or []
    return redirect_uri in allowed


# ── OAuth 2.0 Protected Resource Metadata (RFC 9728) ────────────────
# REQUIRED by MCP spec 2025-06-18. VS Code checks this FIRST.


@router.get("/.well-known/oauth-protected-resource")
async def oauth_protected_resource(request: Request):
    """Return OAuth 2.0 Protected Resource Metadata (RFC 9728).

    VS Code discovers this endpoint FIRST when connecting to a protected
    MCP server. It tells the client which authorization server to use
    and what bearer methods are supported.
    """
    base = _base_url(request)
    return JSONResponse(
        content={
            "resource": base,
            "authorization_servers": [base],
            "bearer_methods_supported": ["header"],
            "scopes_supported": ["mcp:*"],
        }
    )


@router.get("/.well-known/oauth-protected-resource/{path:path}")
async def oauth_protected_resource_path(request: Request, path: str):
    """Handle path-specific Protected Resource Metadata lookups.

    VS Code may append the MCP server path to the well-known URI
    per RFC 9728 (e.g., /.well-known/oauth-protected-resource/gateway/org/mcp/server).
    We return the same metadata for all paths.
    """
    base = _base_url(request)
    return JSONResponse(
        content={
            "resource": base,
            "authorization_servers": [base],
            "bearer_methods_supported": ["header"],
            "scopes_supported": ["mcp:*"],
        }
    )


# ── OAuth 2.0 Authorization Server Metadata (RFC 8414) ──────────────


@router.get("/.well-known/oauth-authorization-server")
async def oauth_metadata(request: Request):
    """Return OAuth 2.0 Authorization Server Metadata (RFC 8414).

    MCP clients discover this endpoint to find authorization, token,
    and registration URLs. Per MCP spec, the base URL is the MCP server
    URL with path stripped.
    """
    base = _base_url(request)
    return JSONResponse(
        content={
            "issuer": base,
            "authorization_endpoint": f"{base}/oauth/authorize",
            "token_endpoint": f"{base}/oauth/token",
            "registration_endpoint": f"{base}/oauth/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"],
            "token_endpoint_auth_methods_supported": ["none"],
            "code_challenge_methods_supported": ["S256"],
            "scopes_supported": ["mcp:*"],
        }
    )


# ── Dynamic Client Registration (RFC 7591) ──────────────────────────


@router.post("/oauth/register")
async def oauth_register(request: Request):
    """Handle dynamic client registration (RFC 7591).

    MCP clients call this automatically before starting the OAuth flow
    to obtain a client_id without user interaction.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    client_id = str(uuid.uuid4())
    client_name = body.get("client_name", "MCP Client")
    redirect_uris = body.get("redirect_uris", [])

    _registered_clients[client_id] = {
        "client_id": client_id,
        "client_name": client_name,
        "redirect_uris": redirect_uris,
        "created_at": time.time(),
    }

    LOG.info("OAuth client registered: %s (%s)", client_id[:8], client_name)

    return JSONResponse(
        content={
            "client_id": client_id,
            "client_name": client_name,
            "redirect_uris": redirect_uris,
            "token_endpoint_auth_method": "none",
        },
        status_code=201,
    )


# ── Authorization Endpoint ──────────────────────────────────────────


@router.get("/oauth/authorize")
async def oauth_authorize_page(request: Request):
    """Render the authorization page where user enters their Gateway API Key.

    VS Code opens this in a browser automatically as part of the OAuth flow.
    The user pastes their Gateway API key, submits the form, and the browser
    redirects back to VS Code with an authorization code.
    """
    params = dict(request.query_params)

    client_id = params.get("client_id", "")
    redirect_uri = params.get("redirect_uri", "")
    state = params.get("state", "")
    code_challenge = params.get("code_challenge", "")
    code_challenge_method = params.get("code_challenge_method", "S256")
    response_type = params.get("response_type", "code")

    if response_type != "code":
        return JSONResponse(
            content={"error": "unsupported_response_type"},
            status_code=400,
        )

    if not _validate_redirect_uri(redirect_uri):
        return JSONResponse(
            content={
                "error": "invalid_request",
                "error_description": "Invalid redirect_uri",
            },
            status_code=400,
        )

    # AU2-01: bind the redirect_uri to the registered client (not just the scheme).
    if not _redirect_uri_allowed_for_client(client_id, redirect_uri):
        return JSONResponse(
            content={
                "error": "invalid_request",
                "error_description": "redirect_uri not registered for this client",
            },
            status_code=400,
        )

    # AU2-02: PKCE is MANDATORY (MCP 2025-06-18 / OAuth 2.1). Requiring a code_challenge
    # here means every issued auth code is PKCE-bound, so a stolen/leaked code cannot be
    # redeemed without the matching verifier (the token endpoint only verified PKCE when a
    # challenge happened to be present — an optional-PKCE downgrade).
    if not code_challenge or code_challenge_method != "S256":
        return JSONResponse(
            content={
                "error": "invalid_request",
                "error_description": "PKCE required: code_challenge with S256 method",
            },
            status_code=400,
        )

    base = _base_url(request)

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>ZeroShield — Authorize MCP Access</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            color: #e2e8f0;
        }}
        .container {{
            background: #1e293b;
            border: 1px solid #334155;
            border-radius: 16px;
            padding: 40px;
            width: 100%;
            max-width: 440px;
            box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);
        }}
        .logo {{
            text-align: center;
            margin-bottom: 24px;
        }}
        .logo h1 {{
            font-size: 24px;
            font-weight: 700;
            background: linear-gradient(135deg, #38bdf8, #818cf8);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        .logo p {{
            color: #94a3b8;
            font-size: 14px;
            margin-top: 4px;
        }}
        .info {{
            background: #0f172a;
            border: 1px solid #334155;
            border-radius: 8px;
            padding: 12px 16px;
            margin-bottom: 24px;
            font-size: 13px;
            color: #94a3b8;
            line-height: 1.5;
        }}
        .info strong {{ color: #e2e8f0; }}
        label {{
            display: block;
            font-size: 14px;
            font-weight: 500;
            margin-bottom: 8px;
            color: #cbd5e1;
        }}
        input[type="password"] {{
            width: 100%;
            padding: 12px 16px;
            background: #0f172a;
            border: 1px solid #475569;
            border-radius: 8px;
            color: #e2e8f0;
            font-size: 15px;
            font-family: 'SF Mono', Monaco, monospace;
            outline: none;
            transition: border-color 0.2s;
        }}
        input[type="password"]:focus {{
            border-color: #38bdf8;
            box-shadow: 0 0 0 3px rgba(56, 189, 248, 0.15);
        }}
        button {{
            width: 100%;
            padding: 12px;
            margin-top: 20px;
            background: linear-gradient(135deg, #2563eb, #4f46e5);
            color: white;
            border: none;
            border-radius: 8px;
            font-size: 15px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
        }}
        button:hover {{ opacity: 0.9; transform: translateY(-1px); }}
        button:disabled {{
            background: #475569;
            cursor: not-allowed;
            transform: none;
        }}
        .footer {{
            text-align: center;
            margin-top: 20px;
            font-size: 12px;
            color: #64748b;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="logo">
            <h1>ZeroShield Gateway</h1>
            <p>MCP Server Authorization</p>
        </div>
        <div class="info">
            An MCP client is requesting access to your ZeroShield gateway.<br/>
            Enter your <strong>Gateway API Key</strong> to authorize the connection.
        </div>
        <form method="POST" action="{html.escape(base)}/oauth/authorize" id="authForm">
            <input type="hidden" name="client_id" value="{html.escape(client_id)}" />
            <input type="hidden" name="redirect_uri" value="{html.escape(redirect_uri)}" />
            <input type="hidden" name="state" value="{html.escape(state)}" />
            <input type="hidden" name="code_challenge" value="{html.escape(code_challenge)}" />
            <input type="hidden" name="code_challenge_method" value="{html.escape(code_challenge_method)}" />
            <input type="hidden" name="response_type" value="code" />
            <label for="api_key">Gateway API Key</label>
            <input type="password" id="api_key" name="api_key"
                   placeholder="zsk_..." required autofocus />
            <button type="submit" id="submitBtn">Authorize Connection</button>
        </form>
        <div class="footer">
            This key is only used to authenticate your MCP connection.
        </div>
    </div>
    <script>
        document.getElementById('authForm').addEventListener('submit', function() {{
            var btn = document.getElementById('submitBtn');
            btn.disabled = true;
            btn.textContent = 'Authorizing\u2026';
        }});
    </script>
</body>
</html>"""

    return HTMLResponse(content=html_content)


@router.post("/oauth/authorize")
async def oauth_authorize_submit(request: Request):
    """Process the authorization form: validate the API key and redirect
    back to the MCP client with an authorization code."""
    # Parse form data manually (avoids python-multipart dependency)
    body = await request.body()
    from urllib.parse import parse_qs
    parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True)

    api_key = parsed.get("api_key", [""])[0].strip()
    client_id = parsed.get("client_id", [""])[0]
    redirect_uri = parsed.get("redirect_uri", [""])[0]
    state = parsed.get("state", [""])[0]
    code_challenge = parsed.get("code_challenge", [""])[0]
    code_challenge_method = parsed.get("code_challenge_method", ["S256"])[0]

    if not _validate_redirect_uri(redirect_uri):
        return JSONResponse(
            content={"error": "invalid_request", "error_description": "Invalid redirect_uri"},
            status_code=400,
        )

    # AU2-01: bind the redirect_uri to the registered client (the code-issuing path — the
    # security-critical one: this is where the auth code that unlocks the API key is minted).
    if not _redirect_uri_allowed_for_client(client_id, redirect_uri):
        return JSONResponse(
            content={"error": "invalid_request",
                     "error_description": "redirect_uri not registered for this client"},
            status_code=400,
        )

    # AU2-02: PKCE is MANDATORY — never issue an auth code without a bound S256 challenge.
    if not code_challenge or code_challenge_method != "S256":
        return JSONResponse(
            content={"error": "invalid_request",
                     "error_description": "PKCE required: code_challenge with S256 method"},
            status_code=400,
        )

    # ── Validate the API key against Redis ──
    import redis.asyncio as aioredis

    redis_url = os.environ.get("GATEWAY_REDIS_URL", "redis://localhost:6379/0")

    try:
        from middleware import validate_api_key
    except ImportError:
        from .middleware import validate_api_key

    try:
        pool = aioredis.ConnectionPool.from_url(
            redis_url, decode_responses=True, max_connections=50
        )
        client = aioredis.Redis(connection_pool=pool)
        auth_context, error = await validate_api_key(api_key, client)
        await client.aclose()
        await pool.disconnect()
    except Exception as exc:
        LOG.error("OAuth authorize — Redis error: %s", exc)
        qs = urlencode({
            "error": "server_error",
            "error_description": "Authentication service unavailable",
            "state": state,
        })
        return RedirectResponse(url=f"{redirect_uri}?{qs}", status_code=302)

    if error:
        qs = urlencode({
            "error": "access_denied",
            "error_description": error.get("message", "Invalid API key"),
            "state": state,
        })
        return RedirectResponse(url=f"{redirect_uri}?{qs}", status_code=302)

    # ── Issue authorization code ──
    _cleanup_expired()
    auth_code = secrets.token_urlsafe(32)
    _auth_codes[auth_code] = {
        "api_key": api_key,
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
        "created_at": time.time(),
    }

    LOG.info(
        "OAuth auth code issued for client=%s org=%s",
        client_id[:8] if client_id else "?",
        getattr(auth_context, "org_slug", "?"),
    )

    qs = urlencode({"code": auth_code, "state": state})
    return RedirectResponse(url=f"{redirect_uri}?{qs}", status_code=302)


# ── Token Endpoint ──────────────────────────────────────────────────


@router.post("/oauth/token")
async def oauth_token(request: Request):
    """Exchange an authorization code for an access token.

    The access_token returned is the user's actual Gateway API Key,
    which means the existing AuthMiddleware validates it seamlessly.
    """
    content_type = request.headers.get("content-type", "")
    if "application/x-www-form-urlencoded" in content_type:
        raw_body = await request.body()
        from urllib.parse import parse_qs
        parsed = parse_qs(raw_body.decode("utf-8"), keep_blank_values=True)
        body = {k: v[0] for k, v in parsed.items()}
    else:
        try:
            body = await request.json()
        except Exception:
            body = {}

    grant_type = str(body.get("grant_type", ""))
    code = str(body.get("code", ""))
    redirect_uri = str(body.get("redirect_uri", ""))
    client_id = str(body.get("client_id", ""))
    code_verifier = str(body.get("code_verifier", ""))

    if grant_type != "authorization_code":
        return JSONResponse(
            content={"error": "unsupported_grant_type"},
            status_code=400,
        )

    _cleanup_expired()

    stored = _auth_codes.pop(code, None)
    if not stored:
        return JSONResponse(
            content={
                "error": "invalid_grant",
                "error_description": "Invalid or expired authorization code",
            },
            status_code=400,
        )

    # Verify client_id
    if stored["client_id"] != client_id:
        return JSONResponse(
            content={
                "error": "invalid_grant",
                "error_description": "Client ID mismatch",
            },
            status_code=400,
        )

    # Verify redirect_uri
    if stored["redirect_uri"] != redirect_uri:
        return JSONResponse(
            content={
                "error": "invalid_grant",
                "error_description": "Redirect URI mismatch",
            },
            status_code=400,
        )

    # Verify PKCE (S256) — MANDATORY (AU2-02). Authorize now refuses to issue a code
    # without a bound challenge, so a stored code ALWAYS carries one; require it here too
    # (fail-closed) so a code can never be redeemed without proving possession of the
    # verifier — closing the stolen/leaked-code-without-PKCE redemption path.
    stored_challenge = stored.get("code_challenge") or ""
    if not stored_challenge or not code_verifier:
        return JSONResponse(
            content={
                "error": "invalid_grant",
                "error_description": "PKCE required: missing code_challenge or code_verifier",
            },
            status_code=400,
        )
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    if computed != stored_challenge:
        return JSONResponse(
            content={
                "error": "invalid_grant",
                "error_description": "PKCE verification failed",
            },
            status_code=400,
        )

    LOG.info("OAuth token issued for client=%s", client_id[:8] if client_id else "?")

    # Return the actual gateway API key as the access token.
    # This way the existing AuthMiddleware validates it seamlessly.
    return JSONResponse(
        content={
            "access_token": stored["api_key"],
            "token_type": "Bearer",
            "expires_in": 86400,
            "scope": "mcp:*",
        }
    )
