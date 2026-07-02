"""OAuth proxy for upstream MCP servers (e.g., Linear MCP).

Handles the full OAuth 2.1 dance on behalf of the user so the gateway can
obtain access tokens for MCP servers that require OAuth.  Tokens are stored
per-org to prevent cross-organization leakage.

Flow:
  1. Frontend POSTs /gateway/{org}/mcp/{server}/oauth/start
  2. Gateway probes server → discovers OAuth metadata (RFC 9728 / RFC 8414)
  3. Gateway does dynamic client registration (RFC 7591)
  4. Gateway generates PKCE, returns authorize URL to frontend
  5. Frontend opens popup → user authorizes at OAuth provider
  6. Provider redirects to GET /gateway/oauth/callback
  7. Gateway exchanges code for tokens, stores per-org
  8. Popup posts message to opener and closes
  9. mcp-remote picks up tokens from per-org config dir automatically
"""

import asyncio
import base64
import hashlib
import html
import json
import logging
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode, urlparse

try:
    from _url_guard import is_safe_outbound_url as _is_safe_outbound_url
except ImportError:
    from ._url_guard import is_safe_outbound_url as _is_safe_outbound_url


def _assert_safe_url(url: str) -> None:
    """H4: SSRF guard for endpoints DERIVED from attacker-controlled OAuth metadata
    (registration/token/refresh endpoints). Validating the metadata DOCUMENT url
    does not validate the urls inside it, so each must be re-checked before the
    outbound POST. Raises RuntimeError (the callers treat it as a flow failure)."""
    ok, reason = _is_safe_outbound_url(url or "")
    if not ok:
        raise RuntimeError(f"Unsafe MCP OAuth endpoint rejected: {reason}")

import httpx
import redis.asyncio as aioredis
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

LOG = logging.getLogger("gateway.mcp_oauth_proxy")

router = APIRouter(tags=["MCP Upstream OAuth Proxy"])

# ── Persistent Redis-backed stores ─────────────────────────────────
# Keys:
#   mcp:oauth:flow:{state}            JSON  TTL=_FLOW_TTL
#   mcp:oauth:token:{org}|{srv_url}   JSON  TTL=expires_in or _TOKEN_DEFAULT_TTL
#
# In-memory dicts kept as a fast-path cache (also used as a fallback if Redis
# is unreachable so unit tests / dev-without-redis still work).
_oauth_flows: dict[str, dict] = {}
_oauth_tokens: dict[str, dict] = {}
_FLOW_TTL = 600                        # 10 minutes
_TOKEN_DEFAULT_TTL = 30 * 24 * 3600    # 30 days when provider sends no expires_in

_REDIS_URL = os.environ.get(
    "GATEWAY_REDIS_URL",
    os.environ.get("REDIS_URL", "redis://redis:6379/0"),
)
_REDIS_CLIENT: aioredis.Redis | None = None
_REDIS_LOCK = asyncio.Lock()


async def _get_redis() -> aioredis.Redis | None:
    """Return a process-shared async Redis client, or None if Redis is down."""
    global _REDIS_CLIENT
    if _REDIS_CLIENT is not None:
        return _REDIS_CLIENT
    async with _REDIS_LOCK:
        if _REDIS_CLIENT is not None:
            return _REDIS_CLIENT
        try:
            client = aioredis.from_url(
                _REDIS_URL,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            await client.ping()
            _REDIS_CLIENT = client
            LOG.info("mcp_oauth_proxy: Redis connected at %s", _REDIS_URL)
        except Exception as exc:  # pragma: no cover - infra failure
            LOG.warning(
                "mcp_oauth_proxy: Redis unavailable (%s); using in-memory fallback",
                exc,
            )
            _REDIS_CLIENT = None
    return _REDIS_CLIENT


def _flow_key(state: str) -> str:
    return f"mcp:oauth:flow:{state}"


def _token_redis_key(org_slug: str, server_url: str) -> str:
    return f"mcp:oauth:token:{org_slug}|{server_url}"


# ── CHG-0042: at-rest encryption for OAuth flow state + tokens in Redis ──────
# OAuth access/refresh tokens (and client secrets in the flow record) were stored
# as plaintext JSON in Redis. Redis is internal, but a compromise would expose
# every org's upstream MCP credentials. Enable encryption by setting
# MCP_OAUTH_ENCRYPTION_KEY (a urlsafe-base64 32-byte Fernet key). Default OFF =
# plaintext (UNCHANGED behaviour). ``_enc_loads`` transparently reads encrypted
# values, cipher-absent plaintext, AND legacy plaintext written before the key was
# set (Fernet tokens are prefix-detectable), so enabling the key never orphans
# existing tokens.
_FERNET_PREFIX = "gAAAAA"  # urlsafe-b64 of the Fernet version byte (0x80)


def _oauth_cipher():
    key = os.environ.get("MCP_OAUTH_ENCRYPTION_KEY", "").strip()
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet
        return Fernet(key.encode())
    except Exception as exc:  # noqa: BLE001 — bad key must never break token storage
        LOG.warning(
            "mcp_oauth_proxy: MCP_OAUTH_ENCRYPTION_KEY invalid (%s); storing plaintext", exc
        )
        return None


def _enc_dumps(obj) -> str:
    """JSON-serialize + encrypt at rest when a cipher is configured (else plaintext)."""
    raw = json.dumps(obj)
    cipher = _oauth_cipher()
    if cipher is None:
        return raw
    return cipher.encrypt(raw.encode()).decode()


def _enc_loads(raw):
    """Inverse of ``_enc_dumps``: decrypt encrypted values; pass plaintext (incl.
    legacy, pre-key) through. Never raises on a decrypt miss — falls back to JSON."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    cipher = _oauth_cipher()
    if cipher is not None and raw.startswith(_FERNET_PREFIX):
        try:
            return json.loads(cipher.decrypt(raw.encode()).decode())
        except Exception:  # noqa: BLE001 — key rotated / not actually encrypted
            pass
    return json.loads(raw)


async def _flow_save(state: str, flow: dict) -> None:
    _oauth_flows[state] = flow
    rc = await _get_redis()
    if rc is None:
        return
    try:
        await rc.setex(_flow_key(state), _FLOW_TTL, _enc_dumps(flow))
    except Exception as exc:  # pragma: no cover
        LOG.warning("mcp_oauth_proxy: flow save failed: %s", exc)


async def _flow_pop(state: str) -> dict | None:
    flow = _oauth_flows.pop(state, None)
    rc = await _get_redis()
    if rc is None:
        return flow
    try:
        raw = await rc.get(_flow_key(state))
        await rc.delete(_flow_key(state))
        if raw:
            return _enc_loads(raw)
    except Exception as exc:  # pragma: no cover
        LOG.warning("mcp_oauth_proxy: flow pop failed: %s", exc)
    return flow


async def _token_save(org_slug: str, server_url: str, data: dict) -> None:
    """Persist token data with TTL derived from access-token lifetime."""
    _oauth_tokens[_token_key(org_slug, server_url)] = data
    rc = await _get_redis()
    if rc is None:
        return
    expires_at = data.get("expires_at")
    if expires_at:
        ttl = max(int(expires_at - time.time()) + 60, 300)
    else:
        ttl = _TOKEN_DEFAULT_TTL
    # Always keep refresh_token alive longer than access_token so we can rotate.
    if data.get("refresh_token"):
        ttl = max(ttl, _TOKEN_DEFAULT_TTL)
    try:
        await rc.setex(
            _token_redis_key(org_slug, server_url),
            ttl,
            _enc_dumps(data),
        )
    except Exception as exc:  # pragma: no cover
        LOG.warning("mcp_oauth_proxy: token save failed: %s", exc)


async def _token_load(org_slug: str, server_url: str) -> dict | None:
    rc = await _get_redis()
    if rc is not None:
        try:
            raw = await rc.get(_token_redis_key(org_slug, server_url))
            if raw:
                data = _enc_loads(raw)
                _oauth_tokens[_token_key(org_slug, server_url)] = data
                return data
        except Exception as exc:  # pragma: no cover
            LOG.warning("mcp_oauth_proxy: token load failed: %s", exc)
    return _oauth_tokens.get(_token_key(org_slug, server_url))

_GATEWAY_URL = (
    os.environ.get("GATEWAY_PUBLIC_URL", "").strip().rstrip("/")
    or "http://127.0.0.1:8300"
)


# ── Public helpers (used by mcp_proxy.py for --header injection) ────

def _token_key(org_slug: str, server_url: str) -> str:
    return f"{org_slug}|{server_url}"


async def _refresh_token(org_slug: str, server_url: str, data: dict) -> dict | None:
    """Use refresh_token to mint a new access_token. Returns updated data or None."""
    refresh_token = data.get("refresh_token")
    token_endpoint = data.get("token_endpoint")
    client_id = data.get("client_id")
    if not (refresh_token and token_endpoint and client_id):
        return None
    body: dict = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }
    if data.get("client_secret"):
        body["client_secret"] = data["client_secret"]
    if data.get("resource"):
        body["resource"] = data["resource"]
    try:
        _assert_safe_url(token_endpoint)  # H4: token_endpoint is from attacker metadata (persisted, reused on refresh)
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:  # H4: no redirect-SSRF
            resp = await client.post(
                token_endpoint,
                data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if resp.status_code != 200:
            LOG.warning(
                "mcp_oauth_proxy: refresh failed for %s|%s -> HTTP %s %s",
                org_slug, server_url, resp.status_code, resp.text[:200],
            )
            return None
        td = resp.json()
    except Exception as exc:
        LOG.warning("mcp_oauth_proxy: refresh error: %s", exc)
        return None
    expires_in = td.get("expires_in")
    new_data = dict(data)
    new_data["access_token"] = td.get("access_token", data.get("access_token"))
    if td.get("refresh_token"):
        new_data["refresh_token"] = td["refresh_token"]
    new_data["token_type"] = td.get("token_type", data.get("token_type", "bearer"))
    new_data["expires_at"] = (time.time() + expires_in) if expires_in else None
    new_data["obtained_at"] = time.time()
    await _token_save(org_slug, server_url, new_data)
    LOG.info(
        "mcp_oauth_proxy: refreshed access_token for %s|%s (new exp=%s)",
        org_slug, server_url, new_data.get("expires_at"),
    )
    return new_data


async def get_stored_token(org_slug: str, server_url: str) -> str | None:
    """Return a valid OAuth access_token for *org_slug* + *server_url*, or None.

    Reads from Redis (falling back to in-memory). If the access_token has
    expired and a refresh_token is available, performs a refresh.
    """
    data = await _token_load(org_slug, server_url)
    if not data:
        return None
    expires_at = data.get("expires_at")
    if expires_at and time.time() >= expires_at - 30:
        # Try to refresh
        refreshed = await _refresh_token(org_slug, server_url, data)
        if refreshed:
            return refreshed.get("access_token")
        LOG.info(
            "mcp_oauth_proxy: token expired and refresh unavailable for %s|%s",
            org_slug, server_url,
        )
        return None
    return data.get("access_token")


async def has_stored_token(org_slug: str, server_url: str) -> bool:
    """True if *any* OAuth token record exists for this org+server.

    Lets callers distinguish "this server was OAuth-authenticated but the token
    expired and could not be refreshed" (needs re-auth) from "this server was
    never OAuth-authenticated" (nothing to do). Does NOT trigger a refresh.
    """
    return bool(await _token_load(org_slug, server_url))


# ── Internal helpers ────────────────────────────────────────────────

def _callback_url() -> str:
    return f"{_GATEWAY_URL}/gateway/oauth/callback"


def _generate_pkce() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) using S256."""
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


async def _discover_oauth_metadata(server_url: str) -> dict:
    """Probe an MCP server URL and walk the OAuth metadata chain.

    Returns dict with keys: as_metadata, resource_metadata, resource.
    Raises RuntimeError on failure.
    """
    # H4: follow_redirects=False so a redirect to an internal address cannot
    # bypass the is_safe_outbound_url guard applied at the oauth_start entry.
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
        # 1. Probe server — expect 401 or resource metadata hint
        try:
            probe = await client.post(
                server_url,
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                headers={"Content-Type": "application/json"},
            )
        except Exception as exc:
            raise RuntimeError(f"Cannot reach MCP server {server_url}: {exc}")

        # 2. Parse WWW-Authenticate for resource_metadata URL
        www_auth = probe.headers.get("www-authenticate", "")
        resource_metadata_url = None
        if "resource_metadata" in www_auth:
            for part in www_auth.replace(",", " ").split():
                if part.startswith("resource_metadata="):
                    resource_metadata_url = part.split("=", 1)[1].strip().strip('"')
                    break

        if not resource_metadata_url:
            parsed = urlparse(server_url)
            resource_metadata_url = (
                f"{parsed.scheme}://{parsed.netloc}"
                "/.well-known/oauth-protected-resource"
            )

        LOG.info("Resource-metadata URL: %s", resource_metadata_url)

        # 3. Fetch resource metadata
        # H4 (residual): resource_metadata_url is derived from the attacker WWW-
        # Authenticate header — validate before the GET (the entry-point guard +
        # follow_redirects=False do not cover a URL supplied directly here).
        _assert_safe_url(resource_metadata_url)
        try:
            rm = await client.get(resource_metadata_url)
            rm.raise_for_status()
            resource_metadata = rm.json()
        except Exception as exc:
            raise RuntimeError(f"Resource metadata fetch failed: {exc}")

        # 4. Authorization-server metadata
        auth_servers = resource_metadata.get("authorization_servers", [])
        if not auth_servers:
            raise RuntimeError("No authorization_servers in resource metadata")
        as_base = auth_servers[0].rstrip("/")

        # H4 (residual): as_base is from the attacker-controlled resource metadata
        # document — validate before the GET.
        _assert_safe_url(as_base)
        try:
            asm = await client.get(
                f"{as_base}/.well-known/oauth-authorization-server"
            )
            asm.raise_for_status()
            as_metadata = asm.json()
        except Exception as exc:
            raise RuntimeError(f"AS metadata fetch failed: {exc}")

        LOG.info("AS metadata keys: %s", list(as_metadata.keys()))
        return {
            "as_metadata": as_metadata,
            "resource_metadata": resource_metadata,
            "resource": resource_metadata.get("resource"),
        }


def _write_mcp_remote_tokens(
    org_slug: str,
    server_url: str,
    token_data: dict,
    flow: dict,
) -> None:
    """Persist token files in the format mcp-remote expects.

    Writes to several version sub-directories so the files are found
    regardless of which mcp-remote version npx resolves.
    """
    config_dir = Path(f"/tmp/mcp-orgs/{org_slug}/mcp-auth")
    server_hash = hashlib.md5(server_url.encode()).hexdigest()

    for ver in ("0.1.37", "0.1.38", "0.1.39", "0.1.40", "0.1.41", "0.1.42", "0.1.43"):
        vdir = config_dir / f"mcp-remote-{ver}"
        vdir.mkdir(parents=True, exist_ok=True)

        (vdir / f"{server_hash}_tokens.json").write_text(json.dumps({
            "access_token": token_data.get("access_token"),
            "refresh_token": token_data.get("refresh_token"),
            "token_type": token_data.get("token_type", "bearer"),
        }))
        (vdir / f"{server_hash}_client_info.json").write_text(json.dumps({
            "clientId": flow.get("client_id"),
            "clientSecret": flow.get("client_secret", ""),
            "redirectUrl": flow.get("callback_url"),
            "redirectUris": [flow.get("callback_url")],
        }))
        (vdir / f"{server_hash}_code_verifier.txt").write_text(
            flow.get("code_verifier", "")
        )

    LOG.info(
        "Wrote mcp-remote token files for %s (hash=%s, org=%s)",
        server_url, server_hash, org_slug,
    )


# ── Routes ──────────────────────────────────────────────────────────

def _require_org_scope(request: Request, org_slug: str):
    """Auth + org-scope gate for OAuth start/status.

    These routes used to bypass auth entirely (a substring match in the gateway
    middleware), letting any caller probe/initiate OAuth under an arbitrary org's
    token namespace (cross-org breach). They now require a valid gateway key whose
    org matches the URL ``org_slug``. The provider→gateway ``/gateway/oauth/callback``
    redirect is the ONLY OAuth route that stays unauthenticated (no key to send;
    validated by its signed flow ``state``). Returns an error JSONResponse on
    missing auth / org mismatch, else ``None``.
    """
    auth = getattr(getattr(request, "state", None), "auth_context", None)
    if auth is None:
        return JSONResponse(
            {"error": "unauthorized", "message": "Missing authentication."}, 401
        )
    if getattr(auth, "org_slug", None) != org_slug:
        LOG.warning(
            "MCP OAuth org scope mismatch: auth org=%s url org=%s",
            getattr(auth, "org_slug", None), org_slug,
        )
        return JSONResponse(
            {"error": "org_scope_violation",
             "message": "API key organization does not match URL."}, 403
        )
    return None


@router.post("/gateway/{org_slug}/mcp/{server_slug}/oauth/start")
async def oauth_start(org_slug: str, server_slug: str, request: Request):
    """Initiate upstream OAuth flow for a registered MCP server.

    Body: ``{ "server_url": "https://mcp.linear.app/mcp" }``

    Returns: ``{ "authorize_url": "…", "state": "…" }``
    """
    _scope_err = _require_org_scope(request, org_slug)
    if _scope_err is not None:
        return _scope_err
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, 400)

    server_url = (body.get("server_url") or "").strip()
    if not server_url:
        return JSONResponse({"error": "server_url is required"}, 400)
    # H4: SSRF guard. server_url is attacker-supplied on an UNAUTHENTICATED endpoint
    # (middleware bypasses the Bearer check for /oauth/start) and is fetched +
    # its discovered endpoints are POSTed to. The B10 fix added is_safe_outbound_url
    # to the control-plane oauth.py + gateway mcp_proxy discover-tools, but this
    # first-order gateway OAuth-proxy path was missed and still shipped unguarded.
    try:
        from _url_guard import is_safe_outbound_url as _safe_url
    except ImportError:
        from ._url_guard import is_safe_outbound_url as _safe_url
    _ok, _reason = _safe_url(server_url)
    if not _ok:
        return JSONResponse({"error": f"Upstream URL rejected by SSRF guard: {_reason}"}, 400)

    # 1. Discover OAuth metadata
    try:
        metadata = await _discover_oauth_metadata(server_url)
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, 502)

    as_meta = metadata["as_metadata"]

    # 2. Dynamic client registration
    reg_ep = as_meta.get("registration_endpoint")
    cb_url = _callback_url()
    client_id = client_secret = None

    if reg_ep:
        _assert_safe_url(reg_ep)  # H4: registration_endpoint is from attacker metadata
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:  # H4: no redirect-SSRF
            try:
                reg = await client.post(
                    reg_ep,
                    json={
                        "client_name": f"ZeroShield Gateway ({org_slug})",
                        "redirect_uris": [cb_url],
                        "grant_types": ["authorization_code"],
                        "response_types": ["code"],
                        "token_endpoint_auth_method": "client_secret_post",
                    },
                    headers={"Content-Type": "application/json"},
                )
                if reg.status_code in (200, 201):
                    rd = reg.json()
                    client_id = rd.get("client_id")
                    client_secret = rd.get("client_secret")
                    LOG.info("Dynamic client registered: %s", client_id)
                else:
                    LOG.warning(
                        "Client registration failed: %s %s",
                        reg.status_code, reg.text[:300],
                    )
            except Exception as exc:
                LOG.warning("Client registration error: %s", exc)

    if not client_id:
        return JSONResponse(
            {"error": "Dynamic client registration failed or not supported"},
            502,
        )

    # 3. PKCE
    code_verifier, code_challenge = _generate_pkce()

    # 4. Build authorize URL
    state = secrets.token_urlsafe(32)
    auth_ep = as_meta.get("authorization_endpoint")
    if not auth_ep:
        return JSONResponse({"error": "No authorization_endpoint in AS metadata"}, 502)

    params: dict = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": cb_url,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    scopes = as_meta.get("scopes_supported")
    if scopes:
        params["scope"] = " ".join(scopes[:5])
    resource = metadata.get("resource")
    if resource:
        params["resource"] = resource

    authorize_url = f"{auth_ep}?{urlencode(params)}"

    # 5. Persist flow state (Redis with in-memory fallback)
    flow_data = {
        "org_slug": org_slug,
        "server_slug": server_slug,
        "server_url": server_url,
        "client_id": client_id,
        "client_secret": client_secret,
        "code_verifier": code_verifier,
        "token_endpoint": as_meta.get("token_endpoint"),
        "callback_url": cb_url,
        "resource": resource,
        "created_at": time.time(),
    }
    await _flow_save(state, flow_data)

    # Evict expired in-memory flows (Redis auto-expires its own keys)
    now = time.time()
    for k in [k for k, v in _oauth_flows.items() if now - v["created_at"] > _FLOW_TTL]:
        _oauth_flows.pop(k, None)

    return JSONResponse({"authorize_url": authorize_url, "state": state})


@router.get("/gateway/oauth/callback")
async def oauth_callback(request: Request):
    """Handle redirect from upstream OAuth authorization server."""
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    error = request.query_params.get("error")

    if error:
        desc = html.escape(request.query_params.get("error_description", ""))
        return HTMLResponse(
            "<html><body style='font-family:sans-serif;text-align:center;padding:40px'>"
            f"<h2 style='color:#dc2626'>Authorization Failed</h2>"
            f"<p>{html.escape(error)}: {desc}</p>"
            "<script>setTimeout(()=>window.close(),3000)</script>"
            "</body></html>",
            400,
        )

    if not code or not state:
        return HTMLResponse(
            "<html><body style='font-family:sans-serif;text-align:center;padding:40px'>"
            "<h2 style='color:#dc2626'>Invalid Callback</h2>"
            "<p>Missing code or state parameter.</p>"
            "<script>setTimeout(()=>window.close(),3000)</script>"
            "</body></html>",
            400,
        )

    flow = await _flow_pop(state)
    if not flow:
        return HTMLResponse(
            "<html><body style='font-family:sans-serif;text-align:center;padding:40px'>"
            "<h2 style='color:#dc2626'>Session Expired</h2>"
            "<p>OAuth flow expired or already used. Please try again.</p>"
            "<script>setTimeout(()=>window.close(),3000)</script>"
            "</body></html>",
            400,
        )

    token_ep = flow.get("token_endpoint")
    if not token_ep:
        return HTMLResponse(
            "<html><body style='font-family:sans-serif;text-align:center;padding:40px'>"
            "<h2 style='color:#dc2626'>Config Error</h2>"
            "<p>No token endpoint available.</p>"
            "<script>setTimeout(()=>window.close(),3000)</script>"
            "</body></html>",
            500,
        )

    # Exchange code for tokens
    token_body: dict = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": flow["callback_url"],
        "client_id": flow["client_id"],
        "code_verifier": flow["code_verifier"],
    }
    if flow.get("client_secret"):
        token_body["client_secret"] = flow["client_secret"]
    if flow.get("resource"):
        token_body["resource"] = flow["resource"]

    _assert_safe_url(token_ep)  # H4: token_endpoint is from attacker metadata
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:  # H4: no redirect-SSRF
        try:
            tok = await client.post(
                token_ep,
                data=token_body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if tok.status_code != 200:
                LOG.error("Token exchange failed: %s %s", tok.status_code, tok.text[:500])
                return HTMLResponse(
                    "<html><body style='font-family:sans-serif;text-align:center;padding:40px'>"
                    f"<h2 style='color:#dc2626'>Token Exchange Failed</h2>"
                    f"<p>HTTP {tok.status_code}</p>"
                    "<script>setTimeout(()=>window.close(),5000)</script>"
                    "</body></html>",
                    502,
                )
            token_data = tok.json()
        except Exception as exc:
            LOG.error("Token exchange error: %s", exc)
            return HTMLResponse(
                "<html><body style='font-family:sans-serif;text-align:center;padding:40px'>"
                f"<h2 style='color:#dc2626'>Token Exchange Error</h2>"
                f"<p>{html.escape(str(exc))}</p>"
                "<script>setTimeout(()=>window.close(),5000)</script>"
                "</body></html>",
                502,
            )

    # Persist token per-org (Redis with in-memory fallback). Include refresh
    # metadata so any worker can rotate the access_token after expiry.
    org_slug = flow["org_slug"]
    server_url = flow["server_url"]
    server_slug = flow["server_slug"]
    expires_in = token_data.get("expires_in")
    persisted = {
        "access_token": token_data.get("access_token"),
        "refresh_token": token_data.get("refresh_token"),
        "token_type": token_data.get("token_type", "bearer"),
        "expires_at": (time.time() + expires_in) if expires_in else None,
        "server_slug": server_slug,
        "obtained_at": time.time(),
        # Refresh metadata
        "token_endpoint": flow.get("token_endpoint"),
        "client_id": flow.get("client_id"),
        "client_secret": flow.get("client_secret"),
        "resource": flow.get("resource"),
    }
    await _token_save(org_slug, server_url, persisted)
    LOG.info("OAuth tokens persisted for %s (org=%s)", server_url, org_slug)

    # Write token files so mcp-remote picks them up natively
    _write_mcp_remote_tokens(org_slug, server_url, token_data, flow)

    # Kill existing stdio process so next request uses cached tokens
    try:
        from mcp_stdio_adapter import _kill_process, _processes, _registry_lock
        key = f"{org_slug}/{server_slug}"
        async with _registry_lock:
            if key in _processes:
                await _kill_process(key)
                LOG.info("Killed stdio process %s so it restarts with fresh tokens", key)
    except Exception as exc:
        LOG.debug("Could not kill stdio process after OAuth: %s", exc)

    safe_org = html.escape(org_slug)
    safe_server = html.escape(server_slug)
    return HTMLResponse(
        "<html><body style='font-family:sans-serif;text-align:center;padding:40px;"
        "background:#f8fafc'>"
        "<div style='max-width:400px;margin:auto;background:white;padding:32px;"
        "border-radius:12px;box-shadow:0 4px 12px rgba(0,0,0,0.1)'>"
        "<div style='font-size:48px;margin-bottom:16px'>&#x2705;</div>"
        f"<h2 style='color:#059669;margin:0 0 8px'>Authorization Successful</h2>"
        f"<p style='color:#64748b'>Connected to <strong>{safe_server}</strong>"
        f" for <strong>{safe_org}</strong></p>"
        "<p style='color:#94a3b8;font-size:12px;margin-top:16px'>"
        "This window will close automatically&hellip;</p>"
        "</div>"
        "<script>"
        "if(window.opener){"
        f"window.opener.postMessage({{type:'mcp-oauth-complete',"
        f"org:'{safe_org}',server:'{safe_server}'}},'*');"
        "}"
        "setTimeout(()=>window.close(),2000);"
        "</script>"
        "</body></html>"
    )


@router.get("/gateway/{org_slug}/mcp/{server_slug}/oauth/status")
async def oauth_status(org_slug: str, server_slug: str, request: Request):
    """Check whether OAuth tokens exist for a server."""
    _scope_err = _require_org_scope(request, org_slug)
    if _scope_err is not None:
        return _scope_err
    server_url = request.query_params.get("server_url", "")
    if not server_url:
        return JSONResponse({"authorized": False, "reason": "no server_url"})

    data = await _token_load(org_slug, server_url)
    if data and data.get("access_token"):
        expired = bool(data.get("expires_at") and time.time() > data["expires_at"])
        return JSONResponse({
            "authorized": not expired or bool(data.get("refresh_token")),
            "expired": expired,
            "refreshable": bool(data.get("refresh_token")),
            "obtained_at": data.get("obtained_at"),
        })
    return JSONResponse({"authorized": False})
