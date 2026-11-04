"""GW32 / GW17b / GW17c capacity values. The only new capacity literals of the MCP cards.

`runtime/resources.py` is at its size gate, so the MCP cards keep their numbers here, in a second
module `lint/check_capacity_literals.py` allowlists. `admit/`, `edge/` and `dispatch/` reference
these names and derived bounds only; none holds a bare capacity number.

Every constant has a one-line rationale. Every derivation reads only existing public
`ResourceContract` methods and fails closed with `CapacityUnavailable` when a derived bound is
below its minimum. `mcp_policy_bounds()` is contract-free on purpose: the identity cache that
parses a signed key record has no `ResourceContract`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from gateway_v2.domain.locks import FRESH_MS
from gateway_v2.runtime.errors import CapacityUnavailable
from gateway_v2.runtime.resources import ResourceContract

# --- GW17c: per-key policy (contract-free) -------------------------------------------------------
MCP_TOOLS_MAX = 256  # most tools one key's allow-list may name (v1 parity headroom)
MCP_TOOL_NAME_MAX = 128  # longest tool name accepted anywhere (policy, params.name, projector)
MCP_CAP_MAX = 1_000_000  # largest per-key tool-call cap a record may carry
# --- GW17b: credentials ---------------------------------------------------------------------------
CREDENTIAL_MIN_LEN = 8  # shortest storable secret AND the reflection guard's match/prefix length
HEADER_NAME_MAX = 64  # longest custom auth header name
HEADER_VALUE_MAX = 4096  # longest rendered header value
CREDENTIAL_RESPONSE_BYTES = 16 * 1024  # control credential reply cap
REFRESH_RESPONSE_BYTES = 32 * 1024  # upstream token endpoint reply cap
REFRESH_SKEW_S = 30.0  # refresh this long before expiry
REFRESH_TIMEOUT_S = 10.0  # one token-endpoint call
REFRESH_TOTAL_BUDGET_S = 20.0  # fetch + lock + refresh + write-back, one request
REFRESH_POLL_S = 0.25  # lock loser re-fetch interval
NEEDS_REAUTH_AFTER_FAILURES = 3  # consecutive transient refresh failures before needs_reauth
# upstream OAuth setup redirect hops, each re-resolved and pinned
CREDENTIAL_CACHE_SLOTS = 1024  # per-worker credential cache entries
OAUTH_REDIRECT_HOPS = 3
# --- GW17c: cap lease -----------------------------------------------------------------------------
CAP_WINDOW_S = 60.0  # a key's cap window, v1 parity: 60 s (D-6), store TTL driven
CAP_CHUNK_MAX = 64  # largest lease chunk a worker takes from the store
# no acquire retry sooner than this after a failure
CAP_BOOTSTRAP = 1  # cold-worker allowance before the first acquire lands (DD-8)
CAP_RETRY_BACKOFF_S = 1.0
MAX_CAS_TRIES = 16  # optimistic-transaction attempts (the BudgetLease idiom)
# --- GW32: sessions, streams, WebSocket, OAuth ----------------------------------------------------
SESSIONS_PER_KEY = 16
SESSIONS_PER_ORG = 256
SESSIONS_GLOBAL = 4096
SESSION_IDLE_TTL_S = 1800.0
SESSION_MAX_LIFETIME_S = 86_400.0
STREAM_KEEPALIVE_S = 15.0
# identity re-check on an open stream: half the freshness bound
STREAM_RECHECK_S = FRESH_MS / 2_000.0
STREAM_MAX_LIFETIME_S = 3600.0
WS_PER_KEY = 8
WS_PER_ORG = 64
WS_GLOBAL_FRACTION = 0.5  # share of connection_budget() WebSockets may hold per worker
WS_IDLE_S = 300.0
WS_IN_FLIGHT = 1
WS_IDLE_WEIGHT = 0.1  # fraction of one admission slot an idle socket holds (D-8)
WS_SEND_TIMEOUT_S = 10.0
WS_SEND_BACKPRESSURE_BYTES = 1024 * 1024
WS_CLOSE_POLICY = 1008
WS_CLOSE_UNSUPPORTED = 1003
WS_CLOSE_TOO_BIG = 1009
WS_CLOSE_RETRY = 1013
OAUTH_BODY_BYTES = 16 * 1024
OAUTH_REDIRECTS_MAX = 8
OAUTH_CLIENTS_PER_SOURCE = 32
OAUTH_CLIENTS_TOTAL = 4096
OAUTH_CLIENT_TTL_S = 90 * 86_400.0
OAUTH_CLIENT_UNUSED_TTL_S = 3600.0
OAUTH_CODE_TTL_S = 60.0
OAUTH_ACCESS_TTL_S = 3600.0
OAUTH_REFRESH_TTL_S = 30 * 86_400.0
OAUTH_FORM_TTL_S = 600.0
OAUTH_FAIL_MAX = 10
OAUTH_FAIL_WINDOW_S = 300.0
TOKEN_CACHE_SLOTS = 4096
_MIN_STORE_SLOTS = 16


@dataclass(frozen=True, slots=True)
class McpPolicyBounds:
    """Bounds the signed-record parser and projector share (contract-free)."""

    tools_max: int
    tool_name_max: int
    cap_max: int


def mcp_policy_bounds() -> McpPolicyBounds:
    """The per-key policy bounds. No contract: the identity cache has none."""
    return McpPolicyBounds(
        tools_max=MCP_TOOLS_MAX, tool_name_max=MCP_TOOL_NAME_MAX, cap_max=MCP_CAP_MAX
    )


@dataclass(frozen=True, slots=True)
class McpTransportBounds:
    """Sessions, streams, WebSocket and OAuth server bounds."""

    sessions_per_key: int
    sessions_per_org: int
    sessions_global: int
    session_idle_ttl_s: float
    session_max_lifetime_s: float
    stream_keepalive_s: float
    stream_recheck_s: float
    stream_max_lifetime_s: float
    ws_per_key: int
    ws_per_org: int
    ws_global: int
    ws_idle_s: float
    ws_in_flight: int
    ws_idle_weight: float
    ws_send_timeout_s: float
    ws_send_backpressure_bytes: int
    ws_frame_bytes: int
    oauth_body_bytes: int
    oauth_redirects_max: int
    oauth_clients_per_source: int
    oauth_clients_total: int
    oauth_client_ttl_s: float
    oauth_client_unused_ttl_s: float
    oauth_code_ttl_s: float
    oauth_access_ttl_s: float
    oauth_refresh_ttl_s: float
    oauth_form_ttl_s: float
    oauth_fail_max: int
    oauth_fail_window_s: float
    token_cache_ttl_s: float
    token_cache_slots: int


def derive_transport_bounds(contract: ResourceContract) -> McpTransportBounds:
    """Transport bounds. The per-worker WebSocket ceiling follows `connection_budget()`."""
    ws_global = math.floor(contract.connection_budget() * WS_GLOBAL_FRACTION)
    if ws_global < 1:
        raise CapacityUnavailable("connection budget leaves no room for MCP WebSockets")
    ws_per_key = min(WS_PER_KEY, ws_global)
    return McpTransportBounds(
        sessions_per_key=SESSIONS_PER_KEY,
        sessions_per_org=SESSIONS_PER_ORG,
        sessions_global=SESSIONS_GLOBAL,
        session_idle_ttl_s=SESSION_IDLE_TTL_S,
        session_max_lifetime_s=SESSION_MAX_LIFETIME_S,
        stream_keepalive_s=STREAM_KEEPALIVE_S,
        stream_recheck_s=STREAM_RECHECK_S,
        stream_max_lifetime_s=STREAM_MAX_LIFETIME_S,
        ws_per_key=ws_per_key,
        ws_per_org=min(WS_PER_ORG, ws_global),
        ws_global=ws_global,
        ws_idle_s=WS_IDLE_S,
        ws_in_flight=WS_IN_FLIGHT,
        ws_idle_weight=WS_IDLE_WEIGHT,
        ws_send_timeout_s=WS_SEND_TIMEOUT_S,
        ws_send_backpressure_bytes=WS_SEND_BACKPRESSURE_BYTES,
        ws_frame_bytes=contract.mcp_bounds().body_bytes,
        oauth_body_bytes=OAUTH_BODY_BYTES,
        oauth_redirects_max=OAUTH_REDIRECTS_MAX,
        oauth_clients_per_source=OAUTH_CLIENTS_PER_SOURCE,
        oauth_clients_total=OAUTH_CLIENTS_TOTAL,
        oauth_client_ttl_s=OAUTH_CLIENT_TTL_S,
        oauth_client_unused_ttl_s=OAUTH_CLIENT_UNUSED_TTL_S,
        oauth_code_ttl_s=OAUTH_CODE_TTL_S,
        oauth_access_ttl_s=OAUTH_ACCESS_TTL_S,
        oauth_refresh_ttl_s=OAUTH_REFRESH_TTL_S,
        oauth_form_ttl_s=OAUTH_FORM_TTL_S,
        oauth_fail_max=OAUTH_FAIL_MAX,
        oauth_fail_window_s=OAUTH_FAIL_WINDOW_S,
        token_cache_ttl_s=FRESH_MS / 1_000.0,
        token_cache_slots=max(_MIN_STORE_SLOTS, min(TOKEN_CACHE_SLOTS, ws_global * 8)),
    )


@dataclass(frozen=True, slots=True)
class McpCredentialBounds:
    """GW17b credential path bounds."""

    credential_min_len: int
    header_name_max: int
    header_value_max: int
    credential_response_bytes: int
    refresh_response_bytes: int
    refresh_skew_s: float
    refresh_timeout_s: float
    refresh_total_budget_s: float
    refresh_poll_s: float
    needs_reauth_after_failures: int
    credential_cache_slots: int
    credential_cache_ttl_s: float
    oauth_redirect_hops: int


def derive_credential_bounds(contract: ResourceContract) -> McpCredentialBounds:
    """Credential bounds. The cache TTL equals `registry_ttl_s` (R14.3)."""
    ttl = contract.mcp_bounds().registry_ttl_s
    if ttl <= 0:
        raise CapacityUnavailable("registry ttl is not positive")
    return McpCredentialBounds(
        credential_min_len=CREDENTIAL_MIN_LEN,
        header_name_max=HEADER_NAME_MAX,
        header_value_max=HEADER_VALUE_MAX,
        credential_response_bytes=CREDENTIAL_RESPONSE_BYTES,
        refresh_response_bytes=REFRESH_RESPONSE_BYTES,
        refresh_skew_s=REFRESH_SKEW_S,
        refresh_timeout_s=REFRESH_TIMEOUT_S,
        refresh_total_budget_s=REFRESH_TOTAL_BUDGET_S,
        refresh_poll_s=REFRESH_POLL_S,
        needs_reauth_after_failures=NEEDS_REAUTH_AFTER_FAILURES,
        credential_cache_slots=CREDENTIAL_CACHE_SLOTS,
        credential_cache_ttl_s=ttl,
        oauth_redirect_hops=OAUTH_REDIRECT_HOPS,
    )


@dataclass(frozen=True, slots=True)
class CapBounds:
    """GW17c per-key call-cap lease bounds."""

    cap_window_s: float
    chunk_max: int
    bootstrap: int
    retry_backoff_s: float
    max_cas_tries: int
    table_slots: int
    workers: int


def derive_cap_bounds(contract: ResourceContract) -> CapBounds:
    """Cap lease bounds. `chunk` and `low_watermark` derive from these in `admit/mcp_cap.py`."""
    workers = contract.workers()
    slots = contract.metrics_directory_slots()
    if slots < 1:
        raise CapacityUnavailable("cap table has no slots")
    return CapBounds(
        cap_window_s=CAP_WINDOW_S,
        chunk_max=CAP_CHUNK_MAX,
        bootstrap=CAP_BOOTSTRAP,
        retry_backoff_s=CAP_RETRY_BACKOFF_S,
        max_cas_tries=MAX_CAS_TRIES,
        table_slots=slots,
        workers=workers,
    )
