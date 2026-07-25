"""AU2-01 + AU2-02 (lifecycle red-team wf_a29f8f21): OAuth hardening.
 - AU2-01: redirect_uri must be bound to the registered client (not just HTTPS scheme),
   else the auth code (= the user's Gateway API key) can be sent to an attacker host.
 - AU2-02: PKCE is mandatory — authorize refuses to issue a code without an S256
   code_challenge, and token refuses to redeem without a matching verifier.
"""
import base64
import hashlib
from types import SimpleNamespace
from urllib.parse import urlencode, parse_qs, urlparse

import pytest

import mcp_oauth


REG_URI = "https://client.example.com/callback"
CLIENT_ID = "client-abc"


@pytest.fixture(autouse=True)
def _clean_state():
    mcp_oauth._registered_clients.clear()
    mcp_oauth._auth_codes.clear()
    yield
    mcp_oauth._registered_clients.clear()
    mcp_oauth._auth_codes.clear()


def _register(redirect_uris):
    mcp_oauth._registered_clients[CLIENT_ID] = {
        "client_id": CLIENT_ID, "redirect_uris": list(redirect_uris),
    }


def _pkce():
    verifier = "verifier-" + "a" * 50
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _authorize_get_req(params):
    return SimpleNamespace(query_params=params,
                           headers={"host": "gw.example.com"},
                           url=SimpleNamespace(scheme="https"))


async def _get_body(resp):
    import json
    return json.loads(bytes(resp.body))


# ── AU2-01: redirect_uri binding ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_authorize_rejects_unregistered_redirect_uri():
    _register([REG_URI])
    _v, challenge = _pkce()
    req = _authorize_get_req({
        "client_id": CLIENT_ID, "redirect_uri": "https://attacker.evil/steal",
        "response_type": "code", "code_challenge": challenge, "code_challenge_method": "S256",
    })
    resp = await mcp_oauth.oauth_authorize_page(req)
    assert resp.status_code == 400
    assert "not registered" in (await _get_body(resp))["error_description"].lower()


@pytest.mark.asyncio
async def test_authorize_rejects_unregistered_client():
    _v, challenge = _pkce()
    req = _authorize_get_req({
        "client_id": "never-registered", "redirect_uri": REG_URI,
        "response_type": "code", "code_challenge": challenge, "code_challenge_method": "S256",
    })
    resp = await mcp_oauth.oauth_authorize_page(req)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_authorize_accepts_registered_redirect_uri():
    _register([REG_URI])
    _v, challenge = _pkce()
    req = _authorize_get_req({
        "client_id": CLIENT_ID, "redirect_uri": REG_URI,
        "response_type": "code", "code_challenge": challenge, "code_challenge_method": "S256",
    })
    resp = await mcp_oauth.oauth_authorize_page(req)
    assert resp.status_code == 200  # renders the API-key form


# ── AU2-02: mandatory PKCE ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_authorize_rejects_missing_pkce():
    _register([REG_URI])
    req = _authorize_get_req({
        "client_id": CLIENT_ID, "redirect_uri": REG_URI, "response_type": "code",
        # no code_challenge
    })
    resp = await mcp_oauth.oauth_authorize_page(req)
    assert resp.status_code == 400
    assert "pkce" in (await _get_body(resp))["error_description"].lower()


@pytest.mark.asyncio
async def test_authorize_rejects_plain_pkce_method():
    _register([REG_URI])
    req = _authorize_get_req({
        "client_id": CLIENT_ID, "redirect_uri": REG_URI, "response_type": "code",
        "code_challenge": "x", "code_challenge_method": "plain",
    })
    resp = await mcp_oauth.oauth_authorize_page(req)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_token_requires_verifier_even_if_code_had_challenge():
    # A stored code with a challenge cannot be redeemed with no/incorrect verifier.
    _v, challenge = _pkce()
    mcp_oauth._auth_codes["code123"] = {
        "api_key": "gw-secret-key", "client_id": CLIENT_ID, "redirect_uri": REG_URI,
        "code_challenge": challenge, "code_challenge_method": "S256",
        "created_at": __import__("time").time(),
    }
    body = urlencode({"grant_type": "authorization_code", "code": "code123",
                      "redirect_uri": REG_URI, "client_id": CLIENT_ID,
                      "code_verifier": ""}).encode()
    req = SimpleNamespace(headers={"content-type": "application/x-www-form-urlencoded"},
                          body=lambda: _coro(body))
    resp = await mcp_oauth.oauth_token(req)
    assert resp.status_code == 400
    assert "gw-secret-key" not in bytes(resp.body).decode()


@pytest.mark.asyncio
async def test_token_succeeds_with_correct_verifier():
    verifier, challenge = _pkce()
    mcp_oauth._auth_codes["code456"] = {
        "api_key": "gw-secret-key", "client_id": CLIENT_ID, "redirect_uri": REG_URI,
        "code_challenge": challenge, "code_challenge_method": "S256",
        "created_at": __import__("time").time(),
    }
    body = urlencode({"grant_type": "authorization_code", "code": "code456",
                      "redirect_uri": REG_URI, "client_id": CLIENT_ID,
                      "code_verifier": verifier}).encode()
    req = SimpleNamespace(headers={"content-type": "application/x-www-form-urlencoded"},
                          body=lambda: _coro(body))
    resp = await mcp_oauth.oauth_token(req)
    data = await _get_body(resp)
    assert data["access_token"] == "gw-secret-key"


async def _coro(v):
    return v
