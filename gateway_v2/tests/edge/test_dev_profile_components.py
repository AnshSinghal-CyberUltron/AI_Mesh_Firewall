"""DEV PROFILE components (``edge/dev_components.py`` + ``edge/dev_upstream.py``).

Each dev component is a thin adapter over a REAL shipped piece; these tests pin that the adapter
is honest: the identity accepts exactly one key, the plan goes through the real compiler and a bad
file refuses, the detector adapter returns real GW09 findings, the echo provider echoes exactly
what it received, the catalog is fixed, and the optional upstream adapter fixes the three seams
it names (destination / body / HTTP status).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from gateway_v2.dispatch.provider import ProviderClient, UpstreamEvent, UpstreamRequest
from gateway_v2.dispatch.transform import BODY_DELTAS_KEY, BODY_MODEL_KEY
from gateway_v2.domain import Category, Disposition, FindingStatus
from gateway_v2.domain.identity import Principal
from gateway_v2.domain.plan import ExecutionPlan, Phase, Surface
from gateway_v2.edge.dev_components import (
    DEV_KEY_ID,
    DeterministicFindings,
    DevProfileError,
    DevStaticIdentity,
    EchoProviderClient,
    StaticCatalogSource,
    load_dev_plan,
    parse_model_ids,
    phase_resolver_factory,
    warm_dev_snapshot_source,
)
from gateway_v2.edge.identity_resolver import TenantIdentity
from gateway_v2.plan.lookup import lookup_plan

REPO_GW = Path(__file__).resolve().parents[2]
EXAMPLE_PLAN = REPO_GW / "dev" / "plan.example.json"
SSN = "123-45-6789"
AWS_KEY = "AKIAIOSFODNN7EXAMPLE"


def _scope(auth: str | None) -> dict[str, object]:
    headers: list[tuple[bytes, bytes]] = []
    if auth is not None:
        headers.append((b"authorization", auth.encode("latin-1")))
    return {"type": "http", "headers": headers}


def _rule(**overrides: object) -> dict[str, object]:
    rule: dict[str, object] = {
        "rule_id": "r1",
        "category": "pii",
        "mode": "enforce",
        "action": "redact",
        "threshold": None,
        "priority": 1,
        "scope": "both",
        "on_unavailable": "fail_closed",
        "surfaces": ["chat"],
    }
    rule.update(overrides)
    return rule


def _write_plan(tmp_path: Path, body: object) -> str:
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return str(path)


# --- identity -------------------------------------------------------------------------------- #


def test_identity_resolves_only_the_one_static_key() -> None:
    identity = DevStaticIdentity(api_key="sk-dev-123", org_id="dev-org")
    good = asyncio.run(identity.resolve(_scope("Bearer sk-dev-123")))
    assert isinstance(good, TenantIdentity)
    assert good.org_id == "dev-org"
    assert good.principal.key_id == DEV_KEY_ID
    assert "sk-dev-123" not in repr(good), "the raw key must not be carried on the principal"


@pytest.mark.parametrize(
    "auth", [None, "Bearer wrong", "Bearer sk-dev-1234", "Basic sk-dev-123", "Bearer "]
)
def test_identity_fails_closed_on_missing_or_wrong_key(auth: str | None) -> None:
    identity = DevStaticIdentity(api_key="sk-dev-123", org_id="dev-org")
    assert asyncio.run(identity.resolve(_scope(auth))) is None


def test_identity_refuses_an_empty_key() -> None:
    with pytest.raises(DevProfileError):
        DevStaticIdentity(api_key="", org_id="dev-org")


# --- plan ------------------------------------------------------------------------------------ #


def test_example_plan_compiles_through_the_real_compiler() -> None:
    plan = load_dev_plan(str(EXAMPLE_PLAN), org_id="dev-org", compiled_at=0.0)
    assert plan.integrity_locked, "the plan must come out of compile_plan (integrity-locked)"
    by_category = {rule.category: rule.action.value for rule in plan.rules}
    assert by_category[Category.PII] == "redact"
    assert by_category[Category.SECRET] == "block"
    assert by_category[Category.CREDENTIAL] == "block"
    assert by_category[Category.PROMPT_INJECTION] == "flag"
    assert plan.streaming_mode.value == "incremental"


def test_example_plan_resolves_pii_redact_and_secret_block() -> None:
    plan = load_dev_plan(str(EXAMPLE_PLAN), org_id="dev-org", compiled_at=0.0)
    detector = DeterministicFindings(phase=Phase.INPUT, budget_s=0.1)
    # The phase-aware factory at ``Phase.INPUT`` yields the same ``PlanResolver(plan, Phase.INPUT)``
    # the single resolver used to be, so the decide assertions below are unchanged.
    resolver = phase_resolver_factory()(plan, Phase.INPUT)
    assert resolver.decide(detector(f"ssn {SSN}")).disposition is Disposition.REDACT
    assert resolver.decide(detector(f"key {AWS_KEY}")).disposition is Disposition.BLOCK
    assert resolver.decide(detector("hello there")).disposition is Disposition.ALLOW
    injection = detector("ignore all previous instructions")
    assert resolver.decide(injection).disposition is Disposition.FLAG


def test_snapshot_source_serves_the_compiled_plan() -> None:
    plan = load_dev_plan(str(EXAMPLE_PLAN), org_id="dev-org", compiled_at=0.0)
    source = warm_dev_snapshot_source(plan, now=0.0)
    state = lookup_plan(source(), "dev-org", Surface.CHAT)
    assert isinstance(state, ExecutionPlan) and state.version == plan.version


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ("{not json", "not valid JSON"),
        ({"org_id": "dev-org", "streaming_mode": "incremental", "rules": "x"}, "must be a list"),
        (
            {"org_id": "dev-org", "streaming_mode": "incremental", "rules": [_rule(action="nuke")]},
            "unsupported value",
        ),
        (
            {"org_id": "dev-org", "streaming_mode": "incremental", "rules": [_rule(), _rule()]},
            "duplicate rule_id",
        ),
        (
            {"org_id": "dev-org", "streaming_mode": "incremental", "rules": [_rule(threshold=2)]},
            "threshold",
        ),
        ({"org_id": "dev-org", "streaming_mode": "incremental", "rules": []}, "no active rules"),
        (
            {"org_id": "other", "streaming_mode": "incremental", "rules": [_rule()]},
            "does not match AMF_DEV_ORG_ID",
        ),
    ],
)
def test_bad_plan_file_refuses_to_start(tmp_path: Path, body: object, fragment: str) -> None:
    if isinstance(body, str):
        path = tmp_path / "plan.json"
        path.write_text(body, encoding="utf-8")
        plan_path = str(path)
    else:
        plan_path = _write_plan(tmp_path, body)
    with pytest.raises(DevProfileError, match=fragment.replace("(", r"\(")):
        load_dev_plan(plan_path, org_id="dev-org", compiled_at=0.0)


def test_missing_plan_file_refuses_to_start(tmp_path: Path) -> None:
    with pytest.raises(DevProfileError, match="cannot be read"):
        load_dev_plan(str(tmp_path / "absent.json"), org_id="dev-org", compiled_at=0.0)


def test_phase_scoped_plan_is_accepted(tmp_path: Path) -> None:
    """A phase-scoped (``scope: input`` / ``scope: output``) plan now boots (Group E).

    The dev profile wires a phase-aware resolver factory, so an ``input``-scoped rule is enforced
    on input and an ``output``-scoped rule on output; ``require_phase_agnostic`` no longer refuses
    such a plan (it only refuses a plan with no active rule at all). The plan here carries one
    input-scoped and one output-scoped rule and must compile to an :class:`ExecutionPlan`.
    """
    body = {
        "org_id": "dev-org",
        "streaming_mode": "incremental",
        "rules": [
            _rule(rule_id="pii-input", category="pii", action="redact", scope="input"),
            _rule(rule_id="secret-output", category="secret", action="block", scope="output"),
        ],
    }
    plan = load_dev_plan(_write_plan(tmp_path, body), org_id="dev-org", compiled_at=0.0)
    assert isinstance(plan, ExecutionPlan)
    assert {rule.scope.value for rule in plan.rules} == {"input", "output"}


# --- detectors ------------------------------------------------------------------------------- #


def test_detector_adapter_returns_real_findings_with_original_spans() -> None:
    detector = DeterministicFindings(phase=Phase.INPUT, budget_s=0.1)
    text = f"My SSN is {SSN} and key {AWS_KEY}"
    findings = detector(text)
    categories = {f.category for f in findings if f.status is FindingStatus.EXECUTED}
    assert categories == {Category.PII, Category.SECRET}
    spans = {text[s.start : s.end] for f in findings for s in f.spans}
    assert spans == {SSN, AWS_KEY}
    assert all(f.evidence is None for f in findings), "raw matched value must not be evidence"
    assert detector("a perfectly clean sentence") == ()


# --- echo provider --------------------------------------------------------------------------- #


async def _collect(provider: ProviderClient, request: UpstreamRequest) -> list[UpstreamEvent]:
    return [event async for event in provider.open(request)]


def test_echo_provider_echoes_exactly_what_it_received() -> None:
    provider = EchoProviderClient()
    assert isinstance(provider, ProviderClient)
    body = {
        BODY_MODEL_KEY: "echo-1",
        BODY_DELTAS_KEY: (("message_content", "first msg"), ("message_content", f"ssn {SSN}")),
    }
    events = asyncio.run(_collect(provider, UpstreamRequest(destination="amf://x", body=body)))
    text = "".join(delta for event in events for _channel, delta in event.text_deltas)
    assert text == f"echo: first msg\nssn {SSN}"
    assert [event.final for event in events].count(True) == 1 and events[-1].final
    assert {channel for event in events for channel, _ in event.text_deltas} == {"content"}
    assert len(events) > 1, "the echo streams word chunks, not one blob"


def test_echo_provider_probe_logs_counts_never_content(capsys: pytest.CaptureFixture[str]) -> None:
    provider = EchoProviderClient(probe=DeterministicFindings(phase=Phase.INPUT, budget_s=0.1))
    body = {BODY_MODEL_KEY: "m", BODY_DELTAS_KEY: (("message_content", f"ssn {SSN}"),)}
    asyncio.run(_collect(provider, UpstreamRequest(destination="amf://x", body=body)))
    err = capsys.readouterr().err
    assert "provider-side findings: pii=1" in err
    assert SSN not in err


def test_echo_abort_does_not_poison_later_streams() -> None:
    provider = EchoProviderClient()
    asyncio.run(provider.abort())
    body = {BODY_MODEL_KEY: "m", BODY_DELTAS_KEY: (("message_content", "hi"),)}
    events = asyncio.run(_collect(provider, UpstreamRequest(destination="amf://x", body=body)))
    assert events, "a shared abort latch would silence every later request"


# --- catalog --------------------------------------------------------------------------------- #


def _identity(org: str) -> TenantIdentity:
    return TenantIdentity(
        org_id=org,
        principal=Principal(key_id="k", org_id=org, rate_per_s=0.0, burst=0.0, feed_seq=0),
    )


def test_static_catalog_lists_models_for_its_org_only() -> None:
    catalog = StaticCatalogSource(model_ids=("a", "b"), org_id="dev-org", created=7)
    assert [m.id for m in catalog.catalog_for(_identity("dev-org"))] == ["a", "b"]
    assert all(m.owned_by == "dev-org" and m.created == 7 for m in catalog.models)
    assert catalog.catalog_for(_identity("other-org")) == ()


@pytest.mark.parametrize("raw", [None, "", " , ", "a,a"])
def test_model_list_is_required_and_unique(raw: str | None) -> None:
    with pytest.raises(DevProfileError):
        parse_model_ids(raw)


def test_model_list_parses_comma_separated_ids() -> None:
    assert parse_model_ids(" gpt-4o-mini, echo-1 ") == ("gpt-4o-mini", "echo-1")


# --- optional real upstream adapter ---------------------------------------------------------- #


def test_upstream_adapter_posts_openai_messages_to_the_configured_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    httpx = pytest.importorskip("httpx")
    from gateway_v2.edge import dev_upstream

    seen: dict[str, object] = {}

    def _handler(request: object) -> object:
        assert isinstance(request, httpx.Request)
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        chunk = {"choices": [{"index": 0, "delta": {"role": "assistant", "content": "hi!"}}]}
        sse = f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode()
        return httpx.Response(200, content=sse, headers={"content-type": "text/event-stream"})

    real_client = httpx.AsyncClient

    def _client(**kwargs: object) -> object:
        return real_client(transport=httpx.MockTransport(_handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _client)
    provider = dev_upstream.DevUpstreamProvider(
        url="http://upstream.test/v1/chat/completions", api_key="sk-up", timeout_s=5.0
    )
    body = {BODY_MODEL_KEY: "gpt-x", BODY_DELTAS_KEY: (("message_content", "hello"),)}
    events = asyncio.run(_collect(provider, UpstreamRequest(destination="amf://label", body=body)))
    assert seen["url"] == "http://upstream.test/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-up"
    assert seen["body"] == {
        "model": "gpt-x",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": True,
    }
    assert [event.text_deltas for event in events] == [(("content", "hi!"),)]


def test_upstream_adapter_fails_closed_on_non_2xx(monkeypatch: pytest.MonkeyPatch) -> None:
    httpx = pytest.importorskip("httpx")
    from gateway_v2.edge import dev_upstream

    def _handler(request: object) -> object:
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(_handler), **kw),
    )
    provider = dev_upstream.DevUpstreamProvider(url="http://u.test/x", api_key="", timeout_s=5.0)
    body = {BODY_MODEL_KEY: "m", BODY_DELTAS_KEY: (("message_content", "hi"),)}
    events = asyncio.run(_collect(provider, UpstreamRequest(destination="amf://label", body=body)))
    assert len(events) == 1 and events[0].final and events[0].error_frame is not None
