"""E4: provider-byte proofs from the recorder: ALLOW 1 call w/ original bytes; REDACT 1 call,
raw canary absent; BLOCK 0 calls. Bodies are sent raw so their exact bytes are known."""

from __future__ import annotations

import hashlib
import json

import httpx
import pytest
import redis

from tests.e2e.conftest import (
    CANARY_AWS,
    CANARY_EMAIL,
    CANARY_EMAIL_HIT,
    INJECTION,
    KEY_A,
    KEY_B,
    MODEL,
    REDIS_URL,
    hits,
    records_for,
    rid,
)


def _post(base: str, key: str, body: bytes, request_id: str) -> httpx.Response:
    return httpx.post(f"{base}/v1/chat/completions", content=body, timeout=60,
                      headers={"authorization": f"Bearer {key}", "content-type": "application/json",
                               "x-request-id": request_id})


def _body(text: str, stream: bool = False) -> bytes:
    # deliberately odd-but-valid JSON spacing: ALLOW must forward these exact bytes
    return json.dumps({"model": MODEL, "stream": stream, "max_tokens": 6,
                       "messages": [{"role": "user", "content": text}]}, indent=1).encode()


def _audit(request_id: str, org: str) -> list[dict[str, object]]:
    r = redis.Redis.from_url(REDIS_URL)
    out = []
    for _, fields in r.xrange(f"rv:audit:{org}"):
        rec = json.loads(fields[b"r"])
        if rec["request_id"] == request_id:
            out.append(rec)
    return out


@pytest.mark.parametrize("stream", [False, True])
def test_allow_forwards_original_bytes_once(base: str, stream: bool) -> None:
    r = rid(f"allow-{stream}")
    body = _body("Tell me about rivers.", stream)
    resp = _post(base, KEY_A, body, r)
    assert resp.status_code == 200 and resp.headers["x-rv-disposition"] == "ALLOW"
    recs = records_for(r, expect=1)
    assert len(recs) == 1, recs
    assert recs[0]["body_sha256"] == hashlib.sha256(body).hexdigest()
    assert recs[0]["body_len"] == len(body) and hits(recs[0]) == []


@pytest.mark.parametrize("stream", [False, True])
def test_redact_sends_sanitized_bytes_once(base: str, stream: bool) -> None:
    r = rid(f"redact-{stream}")
    body = _body(f"Please email {CANARY_EMAIL} the report.", stream)
    resp = _post(base, KEY_A, body, r)
    assert resp.status_code == 200 and resp.headers["x-rv-disposition"] == "REDACT"
    recs = records_for(r, expect=1)
    assert len(recs) == 1, recs
    assert hits(recs[0]) == [] and recs[0]["body_sha256"] != hashlib.sha256(body).hexdigest()
    audit = _audit(r, "org-a")
    inp = [a for a in audit if a["phase"] == "input"]
    assert len(inp) == 1 and inp[0]["disposition"] == "REDACT"
    assert str(inp[0]["verification"]).startswith("verified spans=1 residual=0 leaked=0")


def test_monitor_tenant_forwards_canary_untouched(base: str) -> None:
    """Control: org-b's PII rule is MONITOR, so the same canary must reach the provider."""
    r = rid("monitor")
    body = _body(f"Please email {CANARY_EMAIL} the report.")
    resp = _post(base, KEY_B, body, r)
    assert resp.status_code == 200 and resp.headers["x-rv-disposition"] == "ALLOW"
    (rec,) = records_for(r, expect=1)
    assert hits(rec) == [CANARY_EMAIL_HIT] and rec["body_sha256"] == hashlib.sha256(body).hexdigest()


@pytest.mark.parametrize(("text", "stream"), [(INJECTION, False), (INJECTION, True),
                                              (f"use {CANARY_AWS} to deploy", False),
                                              (f"use {CANARY_AWS} to deploy", True)])
def test_block_makes_zero_provider_calls(base: str, text: str, stream: bool) -> None:
    r = rid("block")
    resp = _post(base, KEY_A, _body(text, stream), r)
    assert resp.status_code == 403 and resp.headers["x-rv-disposition"] == "BLOCK"
    assert b"data:" not in resp.content
    assert records_for(r, timeout=2.0) == []
    (inp,) = [a for a in _audit(r, "org-a") if a["phase"] == "input"]
    assert inp["disposition"] == "BLOCK" and inp["deciding_rules"]
    assert [a for a in _audit(r, "org-a") if a["phase"] == "output"] == []
