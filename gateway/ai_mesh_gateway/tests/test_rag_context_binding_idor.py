"""The RAG context-binding id is a CLIENT-SUPPLIED header used as a Redis key.
It must be (1) confined to the ``rag_ctx:`` namespace (no arbitrary-key IDOR) AND
(2) scoped to the CALLER's org — a leaked binding id (it ships in the
X-ZeroShield-RAG-Context-ID RESPONSE header, routinely logged by proxies/CDNs)
must NOT be replayable cross-tenant to read another org's grounding context (#24).
"""
import json
import pytest
import main


class _Auth:
    def __init__(self, oid, pid):
        self.organization_id = oid
        self.project_id = pid


class _State:
    def __init__(self, auth):
        self.auth_context = auth


class _Req:
    def __init__(self, ctx_id, auth=None):
        self.headers = {"X-ZeroShield-RAG-Context-ID": ctx_id} if ctx_id else {}
        self.state = _State(auth if auth is not None else _Auth(1, "default"))


class _Redis:
    def __init__(self):
        self.gets = []

    async def get(self, key):
        self.gets.append(key)
        if key.startswith("rag_ctx:"):
            return json.dumps(["chunk one", "chunk two"])
        # a sensitive non-rag_ctx key (dict payload) — must never be reached
        return json.dumps({"organization_id": 7, "api_key": "sk-secret"})


def _own_key(auth):
    # mirror the generator: rag_ctx:{project_id}:{uuid16}
    return f"rag_ctx:{main._org_ns_project_id(auth)}:" + "a" * 16


async def test_valid_own_org_binding_reads_chunks(monkeypatch):
    r = _Redis()
    monkeypatch.setattr(main, "REDIS_CLIENT", r)
    auth = _Auth(1, "default")
    key = _own_key(auth)
    out = await main._resolve_rag_context_chunks(_Req(key, auth))
    assert out == ["chunk one", "chunk two"]
    assert r.gets == [key]


async def test_cross_tenant_binding_rejected(monkeypatch):
    # org 1 minted this binding; org 2 replays the leaked id -> MUST NOT read it
    r = _Redis()
    monkeypatch.setattr(main, "REDIS_CLIENT", r)
    org1_key = _own_key(_Auth(1, "default"))
    out = await main._resolve_rag_context_chunks(_Req(org1_key, _Auth(2, "default")))
    assert out == []
    assert r.gets == [], "cross-tenant binding id reached Redis (isolation break)"


async def test_old_unscoped_format_rejected(monkeypatch):
    # legacy rag_ctx:{uuid} (no org) must no longer be honored (#24)
    r = _Redis()
    monkeypatch.setattr(main, "REDIS_CLIENT", r)
    out = await main._resolve_rag_context_chunks(_Req("rag_ctx:abc123", _Auth(1, "default")))
    assert out == []
    assert r.gets == []


async def test_length_pinned_no_prefix_collision(monkeypatch):
    # a longer suffix (extra path/colon) under the same org prefix is rejected —
    # the length pin stops a project_id-with-colon prefix collision.
    r = _Redis()
    monkeypatch.setattr(main, "REDIS_CLIENT", r)
    auth = _Auth(1, "default")
    bad = f"rag_ctx:{main._org_ns_project_id(auth)}:" + "a" * 40
    out = await main._resolve_rag_context_chunks(_Req(bad, auth))
    assert out == []
    assert r.gets == []


@pytest.mark.parametrize("evil", [
    "auth:apikey:deadbeef", "vector:provider:9:pinecone", "config:global",
    "rag_ctx", "xrag_ctx:abc", "*", "",
])
async def test_arbitrary_key_never_queried(monkeypatch, evil):
    r = _Redis()
    monkeypatch.setattr(main, "REDIS_CLIENT", r)
    out = await main._resolve_rag_context_chunks(_Req(evil, _Auth(1, "default")))
    assert out == []
    assert r.gets == [], f"non-rag_ctx key {evil!r} reached Redis (IDOR)"
