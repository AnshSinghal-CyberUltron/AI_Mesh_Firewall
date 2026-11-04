"""MCP JSON-RPC surface: ``POST /v1/mcp/{server_slug}`` (GW17, task 6.2).

Security runs through the same pieces as chat: the identity resolver, the one plan read,
admission, the budget lease, the input detector with ``PlanResolver(plan, Phase.INPUT)``,
``stage_upstream`` for a byte-verified redaction, and the output detector with
``PlanResolver(plan, Phase.OUTPUT)``. There is no MCP-only flag and no MCP-only resolver.

The only egress is the broker (``dispatch/mcp_broker.py``). Before it is called the
registry gate, the gateway SSRF check and the stdio allowlist run, and any refusal makes
zero broker calls. The org comes from the API key only, through the org map.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from gateway_v2.admit.grant import Admitted, ShedVerdict
from gateway_v2.dispatch.mcp_broker import BrokerClient, McpServer
from gateway_v2.dispatch.transform import BODY_DELTAS_KEY, GatewayRequest
from gateway_v2.dispatch.url_guard import Resolver, check_remote, check_stdio
from gateway_v2.domain.decision import Decision, Disposition
from gateway_v2.domain.plan import ExecutionPlan, Phase, PlanUnavailable, PlanUnknownTenant, Surface
from gateway_v2.edge import errors
from gateway_v2.edge.asgi_sse import ASGIReceive, ASGIScope, ASGISend, read_body_bounded
from gateway_v2.edge.audit_emit import emit_chat_audit
from gateway_v2.edge.dev_components import bind_surface
from gateway_v2.edge.identity_resolver import TenantIdentity, hashed_bearer_key, identity_of
from gateway_v2.edge.incident_audit import incident_is_refusal
from gateway_v2.edge.input_enforcement import scan_deltas, stage_upstream
from gateway_v2.edge.mcp_console import mcp_console
from gateway_v2.edge.mcp_registry import McpRegistry, OrgMap, ServerEntry, ToolSet
from gateway_v2.edge.mcp_reply import enforce_reply
from gateway_v2.edge.phase_resolvers import resolver_for
from gateway_v2.edge.request_holds import RequestHolds
from gateway_v2.edge.routes import ChatRoute, emit_response
from gateway_v2.edge.wire import jsonrpc
from gateway_v2.edge.wire.mcp import (
    LOCAL_METHODS,
    METHODS,
    Fold,
    check_request_size,
    fold_request,
    rebuild,
    request_targets,
)
from gateway_v2.plan import lookup_plan

__all__ = ("MCP_PATH", "MCP_PREFIX", "McpRoute")

MCP_PATH = "/v1/mcp"
MCP_PREFIX = "/v1/mcp/"
_STDIO = "stdio"
_PROTOCOL_VERSION = "2025-06-18"
_SERVER_INFO: Mapping[str, str] = MappingProxyType({"name": "zeroshield-gateway", "version": "2"})
_CAPABILITIES = ("tools", "resources", "prompts", "completions")
_ACCEPTED = 202
_OK = 200


class _Ctx:
    """What one request learned, for the console and audit. Built per request."""

    __slots__ = (
        "holds", "input_action", "input_rule", "msg_id", "output_action", "output_rule",
        "plan_version", "scope", "server_slug", "tool", "tool_called",
    )

    def __init__(self, server_slug: str, scope: ASGIScope) -> None:
        self.scope = scope
        self.holds = RequestHolds()
        self.msg_id: jsonrpc.JsonRpcId = None
        self.server_slug = server_slug
        self.tool = ""
        self.plan_version = ""
        self.input_action: str | None = None
        self.input_rule = ""
        self.output_action: str | None = None
        self.output_rule = ""
        self.tool_called = False

    def note_input(self, decision: Decision) -> None:
        self.holds.note_input(decision)
        self.input_action = decision.disposition.value
        self.input_rule = decision.deciding_rules[0] if decision.deciding_rules else ""

    def note_output(self, decision: Decision) -> None:
        self.holds.note_output(decision)
        self.output_action = decision.disposition.value
        self.output_rule = decision.deciding_rules[0] if decision.deciding_rules else ""

    def console(self) -> dict[str, object] | None:
        # The console reports decisions; a refusal before the input scan has none to report.
        if self.input_action is None:
            return None
        return mcp_console(
            request_id=self.holds.request_id,
            plan_version=self.plan_version,
            input_action=self.input_action,
            input_rule=self.input_rule,
            tool_called=self.tool_called,
            output_action=self.output_action,
            output_rule=self.output_rule,
        )


class _Refuse(Exception):
    """Stop the request with a declared value-code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class McpRoute:
    """The MCP surface. Holds the booted chat route and the MCP-only dependencies."""

    chat: ChatRoute
    registry: McpRegistry
    broker: BrokerClient
    org_map: OrgMap
    dns: Resolver
    allow_hosts: frozenset[str]
    stdio_allowed: frozenset[str]

    async def handle_bare(self, scope: ASGIScope, receive: ASGIReceive, send: ASGISend) -> None:
        """``POST /v1/mcp`` with no server slug."""
        del receive
        await self._send_error(send, _Ctx("", scope), "mcp_server_not_found")

    async def handle(self, scope: ASGIScope, receive: ASGIReceive, send: ASGISend) -> None:
        """``POST /v1/mcp/{server_slug}``."""
        ctx = _Ctx(_server_slug(scope), scope)
        refused: str | None = None
        try:
            await self._serve(receive, send, ctx)
        except _Refuse as stop:
            refused = stop.code
            await self._send_error(send, ctx, stop.code)
        finally:
            emit_chat_audit(
                self.chat.audit,
                ctx.holds,
                refused,
                route="mcp",
                console=ctx.console(),
                detail={"server_slug": ctx.server_slug, "tool": ctx.tool},
                observer=self.chat.observer,
            )
            ctx.holds.release(self.chat.admission, self.chat.quota)

    async def _serve(self, receive: ASGIReceive, send: ASGISend, ctx: _Ctx) -> None:
        bounds = self.chat.contract.mcp_bounds()
        body = await read_body_bounded(receive, bounds.body_bytes)
        if body is None:
            raise _Refuse("mcp_request_too_large")
        message = jsonrpc.parse_message(body)
        if isinstance(message, jsonrpc.ProtocolError):
            ctx.msg_id = message.id
            raise _Refuse(message.code)
        ctx.msg_id = message.id
        if not ctx.server_slug:
            raise _Refuse("mcp_server_not_found")
        if message.method in LOCAL_METHODS:
            await self._local(send, message)
            return
        if message.method is None:
            await self._refuse_reply_message(message, ctx)
        if message.method not in METHODS:
            raise _Refuse("mcp_method_not_found")
        await self._forward(send, message, ctx)

    async def _local(self, send: ASGISend, message: jsonrpc.JsonRpcMessage) -> None:
        """``initialize``, ``ping`` and ``notifications/initialized``, answered here."""
        if message.is_notification:
            await emit_response(send, errors.HTTPResponse(status=_ACCEPTED, body=b""))
            return
        if message.method == "initialize":
            params = message.params if isinstance(message.params, dict) else {}
            version = params.get("protocolVersion")
            value: object = {
                "protocolVersion": version if isinstance(version, str) else _PROTOCOL_VERSION,
                "capabilities": {name: {} for name in _CAPABILITIES},
                "serverInfo": dict(_SERVER_INFO),
            }
        else:
            value = {}
        await self._send(send, _OK, jsonrpc.result(message.id, value), None)

    async def _refuse_reply_message(self, message: jsonrpc.JsonRpcMessage, ctx: _Ctx) -> None:
        """A client reply (no ``method``) is scanned as input for audit, then refused."""
        identity, plan = await self._tenant(ctx)
        org = identity.org_id
        targets = (((), message.raw.get("result", message.raw.get("error")), "mcp_arguments"),)
        fold = fold_request(targets, self.chat.contract.mcp_bounds())
        if not isinstance(fold, str):
            ctx.holds.note_request(_request_id(org, "reply", message.raw))
            self._scan_input(fold, plan, ctx)
        raise _Refuse("mcp_invalid_request")

    async def _tenant(self, ctx: _Ctx) -> tuple[TenantIdentity, ExecutionPlan]:
        """Identity, then the plan. The org comes from the key only.

        Returns the resolved :class:`TenantIdentity` (not just its ``org_id``) so the budget phase
        can hand the signed ``principal`` to the composed ``QuotaGate``'s per-key GCRA (GW36 S4).
        """
        identity = (outcome := await identity_of(self.chat.identity, ctx.scope)).identity
        if identity is None:
            raise _Refuse(outcome.refusal)
        ctx.holds.note_tenant(identity.org_id)
        self._incident_org_gate(ctx, identity.org_id)
        state = lookup_plan(self.chat.snapshot_source(), identity.org_id, Surface.MCP)
        if isinstance(state, (PlanUnavailable, PlanUnknownTenant)):
            raise _Refuse("plan_unavailable")
        ctx.plan_version = state.version
        return identity, state

    def _incident_org_gate(self, ctx: _Ctx, org_id: str) -> None:
        """GW29 (6.4b): a global or org kill (or a stale feed) stops MCP; org from the key."""
        holds = ctx.holds
        request_id = holds.request_id or _request_id(org_id, "incident", {})
        verdict = self.chat.incident_gate.decide_org(org_id=org_id, request_id=request_id)
        if verdict.refuse_code is None:
            return
        holds.note_request(request_id)
        holds.note_incident_cause(
            verdict.cause or "gate_error", retry_after_s=verdict.retry_after_s
        )
        raise _Refuse(verdict.refuse_code)

    async def _forward(self, send: ASGISend, message: jsonrpc.JsonRpcMessage, ctx: _Ctx) -> None:
        method = message.method or ""
        bounds = self.chat.contract.mcp_bounds()
        identity, plan = await self._tenant(ctx)
        org = identity.org_id
        control_slug = self.org_map.control_slug(org)
        if control_slug is None:
            raise _Refuse("mcp_org_unmapped")
        targets = request_targets(method, message.params)
        if isinstance(targets, str):
            raise _Refuse(targets)
        too_large = check_request_size(message.params, bounds)
        if too_large is not None:
            raise _Refuse(too_large)
        fold = fold_request(targets, bounds)
        if isinstance(fold, str):
            raise _Refuse(fold)
        params = message.params if isinstance(message.params, dict) else {}
        ctx.tool = str(params.get("name") or "") if method == "tools/call" else ""
        ctx.holds.note_request(_request_id(org, method, message.raw))
        await self._admit_and_charge(identity, fold, ctx)
        server, tools = await self._registry_gate(control_slug, method, ctx)
        forwarded = self._stage(method, params, fold, plan, ctx)
        await self._destination_gate(server)
        ctx.holds.mark_dispatched()
        ctx.tool_called = True
        reply = await self.broker.call(
            control_slug, _broker_server(server), method, forwarded, ctx.holds.request_id
        )
        if reply.code is not None:
            raise _Refuse(reply.code)
        envelope = reply.envelope
        if method == "tools/list":
            envelope = _filter_tools(envelope, tools)
        outcome = enforce_reply(
            envelope,
            bounds=bounds,
            detector=bind_surface(
                self.chat.output_detector or self.chat.error_scanner, Surface.MCP
            ),
            resolver=resolver_for(self.chat, plan, Phase.OUTPUT, surface=Surface.MCP),
        )
        if outcome.decision is not None:
            ctx.note_output(outcome.decision)
        if outcome.code is not None:
            raise _Refuse(outcome.code)
        await self._send(send, _OK, jsonrpc.result(ctx.msg_id, outcome.result, ctx.console()), ctx)

    async def _admit_and_charge(self, identity: TenantIdentity, fold: Fold, ctx: _Ctx) -> None:
        org = identity.org_id
        request_id = ctx.holds.request_id
        verdict = await self.chat.admission.admit(
            owner_id=org, request_id=request_id, enqueued_at=self.chat.clock()
        )
        if isinstance(verdict, ShedVerdict):
            raise _Refuse(verdict.code)
        ctx.holds.admitted(request_id)
        chars = sum(len(leaf.text) for leaf in fold.leaves)
        cost = self.chat.contract.estimate_request_tokens(chars, 0, 1)
        # The composed QuotaGate's per-key GCRA needs the resolved principal + the sha256 key hash
        # identity resolved on (GW36 S4). ``hashed_bearer_key`` re-derives the hash from this
        # scope's Authorization header (the production resolver's own key_namer), not from a raw
        # token held elsewhere; a defensive None folds to "" (the budget phase still fails closed).
        budget = await self.chat.quota.evaluate(
            cost,
            request_id=request_id,
            owner=org,
            principal=identity.principal,
            key_hash=hashed_bearer_key(ctx.scope) or "",
        )
        if not isinstance(budget, Admitted):
            raise _Refuse(budget.code)
        ctx.holds.spent(cost, request_id)

    async def _registry_gate(
        self, control_slug: str, method: str, ctx: _Ctx
    ) -> tuple[ServerEntry, ToolSet | None]:
        server = await self.registry.server(control_slug, ctx.server_slug)
        if isinstance(server, str):
            raise _Refuse(server)
        if not server.usable:
            raise _Refuse("mcp_server_not_found")
        if server.needs_auth:
            raise _Refuse("mcp_upstream_auth_unsupported")
        if method not in ("tools/call", "tools/list"):
            return server, None
        tools = await self.registry.tools(control_slug, ctx.server_slug)
        if isinstance(tools, str):
            raise _Refuse(tools)
        if method == "tools/call" and not tools.allows(ctx.tool):
            raise _Refuse("mcp_tool_disabled")
        return server, tools

    def _scan_input(self, fold: Fold, plan: ExecutionPlan, ctx: _Ctx) -> Decision:
        scan = scan_deltas(
            fold.pairs(),
            join_runs=(),
            detector=bind_surface(self.chat.input_detector, Surface.MCP),
            resolver=resolver_for(self.chat, plan, Phase.INPUT, surface=Surface.MCP),
        )
        if scan.refuse_code is not None or scan.decision is None:
            raise _Refuse(scan.refuse_code or "scan_failure")
        ctx.note_input(scan.decision)
        if scan.decision.disposition is Disposition.BLOCK:
            raise _Refuse("input_blocked")
        return scan.decision

    def _stage(
        self, method: str, params: Mapping[str, object], fold: Fold, plan: ExecutionPlan, ctx: _Ctx
    ) -> object:
        """Scan the request; return the params to forward, masked and byte-verified."""
        decision = self._scan_input(fold, plan, ctx)
        if not fold.leaves:
            return dict(params)
        staged = stage_upstream(
            decision,
            GatewayRequest(destination="mcp", model=method, text_deltas=fold.pairs()),
            self.chat.transform,
        )
        if isinstance(staged, str):
            raise _Refuse(staged)
        texts = _verified_texts(staged.body.get(BODY_DELTAS_KEY), len(fold.leaves))
        try:
            return rebuild(dict(params), fold, texts)
        except ValueError as exc:
            raise _Refuse("input_unmaskable") from exc

    async def _destination_gate(self, server: ServerEntry) -> None:
        if server.transport == _STDIO:
            reason = check_stdio(server.command, self.stdio_allowed)
        else:
            reason = await check_remote(server.url, resolver=self.dns, allow_hosts=self.allow_hosts)
        if reason is not None:
            raise _Refuse("mcp_destination_refused")

    async def _send_error(self, send: ASGISend, ctx: _Ctx, code: str) -> None:
        status, body = jsonrpc.error(
            ctx.msg_id,
            code,
            request_id=ctx.holds.request_id or None,
            extra=ctx.console(),
        )
        await self._send(send, status, body, ctx)

    async def _send(self, send: ASGISend, status: int, body: bytes, ctx: _Ctx | None) -> None:
        headers: tuple[tuple[str, str], ...] = (("content-type", "application/json"),)
        if ctx is not None and ctx.holds.request_id:
            headers = (*headers, ("x-request-id", ctx.holds.request_id))
        if ctx is not None and incident_is_refusal(ctx.holds):
            headers = (*headers, *errors.retry_headers(ctx.holds.incident_retry_after_s))
        await emit_response(send, errors.HTTPResponse(status=status, body=body, headers=headers))


def _server_slug(scope: ASGIScope) -> str:
    path = scope.get("path")
    if not isinstance(path, str) or not path.startswith(MCP_PREFIX):
        return ""
    slug = path[len(MCP_PREFIX):]
    return "" if not slug or "/" in slug else slug


def _request_id(org: str, method: str, raw: Mapping[str, object]) -> str:
    content = json.dumps(raw, sort_keys=True, separators=(",", ":"), default=str)
    fingerprint = hashlib.sha256(f"{org}\x1f{method}\x1f{content}".encode()).hexdigest()[:24]
    return f"zs-{org}-{fingerprint}"


def _verified_texts(raw: object, count: int) -> tuple[str, ...]:
    if not isinstance(raw, (list, tuple)) or len(raw) != count:
        raise _Refuse("input_unmaskable")
    texts: list[str] = []
    for pair in raw:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2 or not isinstance(pair[1], str):
            raise _Refuse("input_unmaskable")
        texts.append(pair[1])
    return tuple(texts)


def _broker_server(server: ServerEntry) -> McpServer:
    return McpServer(
        slug=server.slug,
        transport=server.transport,
        url=server.url,
        command=server.command,
        args=server.args,
        env=server.env,
    )


def _filter_tools(envelope: object, tools: ToolSet | None) -> object:
    """Drop tools the registry does not allow before the reply is scanned or returned."""
    if tools is None or not isinstance(envelope, dict):
        return envelope
    result = envelope.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
        return envelope
    kept = [
        tool
        for tool in result["tools"]
        if isinstance(tool, dict)
        and isinstance(tool.get("name"), str)
        and tools.allows(tool["name"])
    ]
    return {**envelope, "result": {**result, "tools": kept}}
