"""GW26: each org's plan is its own key, and the dev key is left untouched."""

from __future__ import annotations

import inspect

import fakeredis

from gateway_v2.detect.conditions import compile_conditions, scan_conditions
from gateway_v2.domain import Disposition, Phase, Surface
from gateway_v2.domain.category import Category
from gateway_v2.edge.dev_plan_feed import (
    PUBLISHED_PLAN_KEY,
    DevPlanFeed,
    choose_published,
    plan_key,
    poll_bodies,
    request_org,
)
from gateway_v2.plan.compiler import compile_plan
from gateway_v2.plan.document import decode_plan_body
from gateway_v2.resolve.resolver import resolve
from tests.plan.test_dev_plan_publish import _load_publisher

_DEV = b"sentinel-dev-plan"


def _policy(code: str, keyword: str) -> dict[str, object]:
    return {
        "code": code,
        "enabled": True,
        "policy_domain": "pipeline",
        "category": "exfil",
        "priority": 200,
        "rules": [
            {
                "id": 1,
                "name": "competitor",
                "enabled": True,
                "rule_type": "keywords",
                "action": "block",
                "priority": 200,
                "pipeline_stage": "",
                "condition": {
                    "keywords": [keyword],
                    "field": "prompt",
                    "surfaces": ["chat"],
                },
            },
            {
                "id": 2,
                "name": "rewrite-me",
                "enabled": True,
                "rule_type": "keywords",
                "action": "rewrite",
                "priority": 1,
                "pipeline_stage": "",
                "condition": {"keywords": ["hidden"], "field": "prompt"},
            },
        ],
    }


def _document(publisher, org_id: str, keyword: str) -> bytes:  # type: ignore[no-untyped-def]
    drafts, unsupported = publisher.policy_drafts([_policy(org_id, keyword)])  # type: ignore[attr-defined]
    assert any("rewrite" in item for item in unsupported)
    assert all(row["action"] != "rewrite" for row in drafts)
    payload = {
        "firewall_enabled": True,
        "pii_detection_enabled": False,
        "output_pii_enabled": False,
        "jailbreak_detection_enabled": True,
    }
    return publisher.document_bytes(org_id, payload, drafts)  # type: ignore[attr-defined]


def test_two_orgs_keep_their_own_keyword_and_the_dev_key() -> None:
    publisher = _load_publisher()
    assert publisher.plan_key("org-a") == plan_key("org-a")
    assert "PUBLISHED_PLAN_KEY" not in inspect.getsource(publisher._write)
    left = _document(publisher, "org-a", "AcmeA")
    right = _document(publisher, "org-b", "AcmeB")
    client = fakeredis.FakeRedis()
    client.set(PUBLISHED_PLAN_KEY, _DEV)
    client.set(publisher.plan_key("org-a"), left)
    client.set(publisher.plan_key("org-b"), right)
    assert client.get(PUBLISHED_PLAN_KEY) == _DEV
    assert choose_published(client.get(publisher.plan_key("org-a")), _DEV) == left
    assert choose_published(None, _DEV) == _DEV

    plan_a = _compiled(left)
    plan_b = _compiled(right)
    assert Category.JAILBREAK in {rule.category for rule in plan_a.rules}
    assert _blocks(plan_a, "buy AcmeA")
    assert not _blocks(plan_a, "buy AcmeB")
    assert _blocks(plan_b, "buy AcmeB")
    assert not _blocks(plan_b, "buy AcmeA")


def test_one_feed_scans_only_the_request_org() -> None:
    publisher = _load_publisher()
    left = _document(publisher, "org-a", "AcmeA")
    right = _document(publisher, "org-b", "AcmeB")
    client = fakeredis.FakeRedis()
    client.set(PUBLISHED_PLAN_KEY, _DEV)
    feed = DevPlanFeed(_compiled(left), now=0.0, peer_orgs=("org-b",))
    assert feed.apply(right, compiled_at=2.0, now=1.0, org_id="org-b") != "rejected"
    assert client.get(PUBLISHED_PLAN_KEY) == _DEV
    fetched = {
        plan_key("org-a"): left,
        plan_key("org-b"): right,
    }
    bodies = poll_bodies("org-a", ("org-b",), fetched, _DEV)
    assert bodies[0] == ("org-a", left)
    assert bodies[1] == ("org-b", right)
    assert client.get(PUBLISHED_PLAN_KEY) == _DEV
    token = request_org.set("org-b")
    try:
        peer = feed.conditions()
        assert not _hits(peer, "buy AcmeA")
        assert _hits(peer, "buy AcmeB")
    finally:
        request_org.reset(token)
    request_org.set(None)
    primary = feed.conditions()
    assert _hits(primary, "buy AcmeA")
    assert not _hits(primary, "buy AcmeB")


def _compiled(raw: bytes):
    document = decode_plan_body(raw)
    return compile_plan(document.org_id, document.drafts, compiled_at=1.0)


def _hits(program, text: str) -> bool:
    scanned = scan_conditions(program, text, budget_s=0.1, phase=Phase.INPUT, surface=Surface.CHAT)
    return bool(scanned.findings)


def _blocks(plan, text: str) -> bool:
    program = compile_conditions(plan.rules)
    scanned = scan_conditions(program, text, budget_s=0.1, phase=Phase.INPUT)
    decision = resolve(scanned.findings, plan, Phase.INPUT, Surface.CHAT)
    return decision.disposition is Disposition.BLOCK
