"""#23: jobs.enqueue_job created a FRESH aioredis client (with a connection pool)
on EVERY call and never closed it — every enqueued job (chat/MCP/RAG async ingest
dispatch, a hot path) leaked a client + pool → Redis connection exhaustion under
load. Fixed: one lazily-created client is reused across calls. These tests lock
the reuse (client created ONCE across many enqueues) and the close helper.
"""
import jobs


class _FakeClient:
    def __init__(self):
        self.lpushes = []
        self.closed = False

    async def lpush(self, queue, value):
        self.lpushes.append((queue, value))
        return 1

    async def aclose(self):
        self.closed = True


async def test_enqueue_job_reuses_single_client(monkeypatch):
    created = []

    def _fake_from_url(url, **kw):
        c = _FakeClient()
        created.append(c)
        return c

    monkeypatch.setattr(jobs.aioredis, "from_url", _fake_from_url)
    jobs._CLIENT = None  # reset module cache for a clean run

    ok1 = await jobs.enqueue_job("vector_ingest", "r1", 1, {"x": 1})
    ok2 = await jobs.enqueue_job("vector_ingest", "r2", 1, {"x": 2})
    ok3 = await jobs.enqueue_job("chat", "r3", 2, {"y": 3})

    assert ok1 and ok2 and ok3
    # THE FIX: one client reused across all three enqueues (was: 3 leaked clients).
    assert len(created) == 1, f"expected 1 reused client, got {len(created)} (per-call leak)"
    assert len(created[0].lpushes) == 3  # all three jobs went through the one client
    await jobs.close_jobs_client()


async def test_close_jobs_client_closes_and_resets(monkeypatch):
    def _fake_from_url(url, **kw):
        return _FakeClient()

    monkeypatch.setattr(jobs.aioredis, "from_url", _fake_from_url)
    jobs._CLIENT = None
    await jobs.enqueue_job("vector_ingest", "r1", 1, {"x": 1})
    client = jobs._CLIENT
    assert client is not None
    await jobs.close_jobs_client()
    assert client.closed is True
    assert jobs._CLIENT is None  # a subsequent enqueue lazily recreates it
