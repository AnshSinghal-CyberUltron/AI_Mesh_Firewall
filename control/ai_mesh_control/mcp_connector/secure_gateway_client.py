"""HTTP client for Enkrypt AI Secure MCP Gateway API.

Proxies guardrail configuration, project/user management, and MCP config
management to the Secure-MCP-Gateway service at SECURE_MCP_GATEWAY_URL.

API reference: https://github.com/enkryptai/secure-mcp-gateway/blob/main/API-Reference.md
"""

import logging
import os
from urllib.parse import urljoin

import requests

logger = logging.getLogger(__name__)

_BASE_URL = os.environ.get("SECURE_MCP_GATEWAY_URL", "http://secure-mcp-gateway:8000")
_TIMEOUT = int(os.environ.get("SECURE_MCP_GATEWAY_TIMEOUT", "15"))


def _headers() -> dict:
    """Build auth headers for Secure MCP Gateway admin API."""
    api_key = os.environ.get("SECURE_MCP_GATEWAY_ADMIN_KEY", "")
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }


def _url(path: str) -> str:
    return urljoin(_BASE_URL.rstrip("/") + "/", path.lstrip("/"))


def is_configured() -> bool:
    """Return True if the Enkrypt Secure MCP Gateway API key is set."""
    return bool(os.environ.get("SECURE_MCP_GATEWAY_ADMIN_KEY", "").strip())


# ── Health ────────────────────────────────────────────────────────────

# The REST API server (api_server.py) runs on port 8001, but the Docker
# image only starts the MCP gateway (gateway.py) on port 8000.  The MCP
# gateway speaks Streamable HTTP at /mcp/ and returns 400 or 406 for
# plain GET requests — any HTTP response means the service is alive.
_REST_API_BASE_URL = os.environ.get(
    "SECURE_MCP_GATEWAY_REST_URL",
    "",  # empty = not configured / same host
)


def health() -> dict:
    """Check Secure MCP Gateway health.

    Probes the MCP Streamable HTTP endpoint (/mcp/) first.  Any HTTP
    response (including 400/406) proves the process is alive.  If a
    separate REST API base URL is configured, also check that.
    """
    # Primary check: MCP Streamable HTTP endpoint
    try:
        resp = requests.get(_url("/mcp/"), timeout=_TIMEOUT)
        # 400 (missing session ID) or 406 (wrong Accept) = service is up
        if resp.status_code in (200, 400, 405, 406):
            return {"status": "healthy", "detail": "MCP endpoint responding"}
        return {"status": "unhealthy", "detail": f"Unexpected status {resp.status_code}"}
    except requests.RequestException as exc:
        logger.warning("Secure MCP Gateway health check failed: %s", exc)
        return {"status": "unreachable", "detail": str(exc)}


# ── Configurations ───────────────────────────────────────────────────
def list_configs() -> list:
    """List all MCP configurations."""
    resp = requests.get(_url("/api/v1/configs"), headers=_headers(), timeout=_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    return data.get("data", data)


def create_config(config_name: str) -> dict:
    """Create a new MCP configuration."""
    resp = requests.post(
        _url("/api/v1/configs"),
        headers=_headers(),
        json={"config_name": config_name},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def get_config(config_id: str) -> dict:
    resp = requests.get(_url(f"/api/v1/configs/{config_id}"), headers=_headers(), timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def delete_config(config_id: str) -> dict:
    resp = requests.delete(_url(f"/api/v1/configs/{config_id}"), headers=_headers(), timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


# ── Servers within configs ───────────────────────────────────────────
def list_config_servers(config_id: str) -> list:
    """List MCP servers within a configuration."""
    resp = requests.get(_url(f"/api/v1/configs/{config_id}/servers"), headers=_headers(), timeout=_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    return data.get("data", data)


def add_server_to_config(config_id: str, server_payload: dict) -> dict:
    """Add an MCP server to a configuration."""
    resp = requests.post(
        _url(f"/api/v1/configs/{config_id}/servers"),
        headers=_headers(),
        json=server_payload,
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def remove_server_from_config(config_id: str, server_name: str) -> dict:
    resp = requests.delete(
        _url(f"/api/v1/configs/{config_id}/servers/{server_name}"),
        headers=_headers(),
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


# ── Guardrails ───────────────────────────────────────────────────────
def update_server_guardrails(config_id: str, server_name: str, guardrails: dict) -> dict:
    """Update input+output guardrails for a server in a config."""
    resp = requests.put(
        _url(f"/api/v1/configs/{config_id}/servers/{server_name}/guardrails"),
        headers=_headers(),
        json=guardrails,
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def update_input_guardrails(config_id: str, server_name: str, policy: dict) -> dict:
    resp = requests.put(
        _url(f"/api/v1/configs/{config_id}/servers/{server_name}/input-guardrails"),
        headers=_headers(),
        json=policy,
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def update_output_guardrails(config_id: str, server_name: str, policy: dict) -> dict:
    resp = requests.put(
        _url(f"/api/v1/configs/{config_id}/servers/{server_name}/output-guardrails"),
        headers=_headers(),
        json=policy,
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


# ── Projects ─────────────────────────────────────────────────────────
def list_projects() -> list:
    resp = requests.get(_url("/api/v1/projects"), headers=_headers(), timeout=_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    return data.get("data", data)


def create_project(project_name: str) -> dict:
    resp = requests.post(
        _url("/api/v1/projects"),
        headers=_headers(),
        json={"project_name": project_name},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def assign_config_to_project(project_id: str, config_name: str) -> dict:
    resp = requests.post(
        _url(f"/api/v1/projects/{project_id}/assign-config"),
        headers=_headers(),
        json={"config_name": config_name},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


# ── Users ─────────────────────────────────────────────────────────────
def list_users() -> list:
    resp = requests.get(_url("/api/v1/users"), headers=_headers(), timeout=_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    return data.get("data", data)


def create_user(email: str) -> dict:
    resp = requests.post(
        _url("/api/v1/users"),
        headers=_headers(),
        json={"email": email},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()
