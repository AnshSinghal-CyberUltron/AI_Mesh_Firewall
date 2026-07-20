"""CHG-0132: proxy_chat's INLINE burst/RPM rate-limit must set its Redis TTL
ATOMICALLY (INCR + EXPIRE NX in one MULTI/EXEC), never the old non-atomic
``INCR; if current == 1: EXPIRE``.

Background: CHG-0062 fixed the orphaned-no-TTL-key leak in
``rate_limit_enforcement.py`` (and the ``_enforce_org_burst_rpm`` shim that the
embeddings/RAG endpoints call), but the chat hot path (``proxy_chat``) kept an
INLINE copy of the counter logic that still used the non-atomic idiom — so the
highest-traffic endpoint was the one left leaking no-TTL keys under
cancellation (client disconnect between the INCR and the EXPIRE). The burst key is
a NEW key every second, so orphans accumulated one per second → unbounded Redis
growth under soak/stress.

The inline block is not a callable function, so this is a source-level guard that
the non-atomic idiom does not reappear anywhere in main.py's rate-limit code and
that the atomic EXPIRE NX is present for both counters. The atomic pattern's
runtime correctness (TTL always set, self-healing, limit still enforced) is proven
by test_rate_limit_atomic_ttl.py for the identical code in rate_limit_enforcement.py.
"""
from pathlib import Path

_MAIN = Path(__file__).resolve().parents[1] / "main.py"


def test_main_ratelimit_has_no_non_atomic_ttl_idiom():
    src = _MAIN.read_text()
    # the old non-atomic tell — must not exist anywhere in main.py
    assert "if current_burst == 1:" not in src, "non-atomic burst TTL idiom reintroduced"
    assert "if current_rpm == 1:" not in src, "non-atomic RPM TTL idiom reintroduced"


def test_main_ratelimit_uses_atomic_expire_nx():
    src = _MAIN.read_text()
    # both counters must (re)set their TTL with EXPIRE NX (self-healing, atomic)
    assert "expire(burst_key, 2, nx=True)" in src, "burst counter missing atomic EXPIRE NX"
    assert "expire(rpm_key, 120, nx=True)" in src, "RPM counter missing atomic EXPIRE NX"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
