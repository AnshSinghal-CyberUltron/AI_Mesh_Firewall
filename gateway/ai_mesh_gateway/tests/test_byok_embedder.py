"""Fail-closed contract for the BYOK embedder: never a zero/garbage vector."""
import math
import random
import sys
import types

import pytest

from byok_embedder import (
    EmbeddingConfigError,
    EmbeddingDegradedError,
    embed_texts,
)


class _FakeData:
    def __init__(self, values):
        self.values = values


class _FakeResp:
    def __init__(self, vecs):
        self.data = [_FakeData(v) for v in vecs]


class _FakeInference:
    def __init__(self, vecs):
        self._vecs = vecs

    def embed(self, model, inputs, parameters):
        return _FakeResp(self._vecs)


class _FakePineconeClient:
    def __init__(self, vecs):
        self.inference = _FakeInference(vecs)


def test_no_model_raises_config_error():
    with pytest.raises(EmbeddingConfigError):
        embed_texts(["x"], embedding_model="")


def test_empty_texts_returns_empty():
    assert embed_texts([], embedding_model="m") == []


def test_pinecone_hosted_route_ok():
    pc = _FakePineconeClient([[0.1, 0.2], [0.3, 0.4]])
    out = embed_texts(["a", "b"], embedding_model="multilingual-e5-large", pinecone_client=pc)
    assert out == [[0.1, 0.2], [0.3, 0.4]]


def test_zero_vector_fails_closed():
    pc = _FakePineconeClient([[0.0, 0.0]])
    with pytest.raises(EmbeddingDegradedError):
        embed_texts(["a"], embedding_model="multilingual-e5-large", pinecone_client=pc)


def test_empty_vector_fails_closed():
    pc = _FakePineconeClient([[]])
    with pytest.raises(EmbeddingDegradedError):
        embed_texts(["a"], embedding_model="multilingual-e5-large", pinecone_client=pc)


def test_count_mismatch_fails_closed():
    pc = _FakePineconeClient([[0.1, 0.2]])  # 1 vector for 2 inputs
    with pytest.raises(EmbeddingDegradedError):
        embed_texts(["a", "b"], embedding_model="multilingual-e5-large", pinecone_client=pc)


def test_dim_mismatch_fails_closed():
    pc = _FakePineconeClient([[0.1, 0.2, 0.3]])
    with pytest.raises(EmbeddingDegradedError):
        embed_texts(
            ["a"], embedding_model="multilingual-e5-large", pinecone_client=pc, expected_dim=2
        )


def test_litellm_route_passes_byok_key(monkeypatch):
    captured = {}

    class _R:
        data = [{"embedding": [0.5, 0.6]}]

    fake = types.ModuleType("litellm")

    def embedding(**kwargs):
        captured.update(kwargs)
        return _R()

    fake.embedding = embedding
    monkeypatch.setitem(sys.modules, "litellm", fake)

    out = embed_texts(
        ["a"], embedding_model="text-embedding-3-small", provider_api_key="sk-org"
    )
    assert out == [[0.5, 0.6]]
    assert captured["model"] == "text-embedding-3-small"
    assert captured["api_key"] == "sk-org"  # BYOK key passed through


def test_litellm_zero_vector_fails_closed(monkeypatch):
    class _R:
        data = [{"embedding": [0.0, 0.0, 0.0]}]

    fake = types.ModuleType("litellm")
    fake.embedding = lambda **kw: _R()
    monkeypatch.setitem(sys.modules, "litellm", fake)
    with pytest.raises(EmbeddingDegradedError):
        embed_texts(["a"], embedding_model="text-embedding-3-small")


# ===========================================================================
# EXTREME fuzz harness: hammer the fail-closed contract.
#
# CONTRACT under test (from byok_embedder docstring):
#   embed_texts NEVER returns a zero/empty/garbage vector. It either returns a
#   list of N vectors (N == len(non-empty inputs)), each:
#     - non-empty
#     - not all-zero
#     - finite (no NaN/inf)            <-- "garbage" per the module's promise
#     - matching expected_dim if given
#   OR it raises (EmbeddingConfigError / EmbeddingDegradedError / re-raised
#   provider error). There is NO third outcome.
# ===========================================================================

# --- provider error types (litellm-style) -------------------------------
class _AuthenticationError(Exception):
    pass


class _RateLimitError(Exception):
    pass


class _TimeoutError(Exception):
    pass


def _is_garbage_vector(vec, expected_dim=None):
    """A vector that should NEVER be returned by a fail-closed embedder."""
    if not vec:  # empty
        return True
    if all(x == 0.0 for x in vec):  # all-zero
        return True
    for x in vec:
        try:
            xf = float(x)
        except (TypeError, ValueError):
            return True  # non-numeric => garbage
        if math.isnan(xf) or math.isinf(xf):  # NaN / inf => semantically junk
            return True
    if expected_dim is not None and len(vec) != expected_dim:
        return True
    return False


def _assert_fail_closed(call, *, n_inputs, expected_dim=None):
    """Run ``call``; assert it either raises or returns CLEAN vectors only.

    Returns ('raised', exc) or ('returned', value). Asserts the contract.
    """
    try:
        out = call()
    except Exception as exc:  # any raise satisfies fail-closed
        return ("raised", exc)
    # If it returned, EVERY vector must be clean and count must match.
    assert isinstance(out, list), f"non-list return: {type(out)!r}"
    assert len(out) == n_inputs, (
        f"FAIL-CLOSED BREACH: returned {len(out)} vectors for {n_inputs} "
        f"inputs without raising"
    )
    for idx, vec in enumerate(out):
        assert not _is_garbage_vector(vec, expected_dim), (
            f"FAIL-CLOSED BREACH: returned garbage vector at index {idx}: "
            f"{vec!r} (expected_dim={expected_dim})"
        )
    return ("returned", out)


def _pinecone(vecs):
    return _FakePineconeClient(vecs)


def _install_litellm(monkeypatch, fn):
    fake = types.ModuleType("litellm")
    fake.embedding = fn
    # error classes some callers introspect
    fake.AuthenticationError = _AuthenticationError
    fake.RateLimitError = _RateLimitError
    fake.Timeout = _TimeoutError
    monkeypatch.setitem(sys.modules, "litellm", fake)
    return fake


# ---------------------------------------------------------------------------
# 1. NaN / inf garbage vectors (the prime suspect: _is_zero_or_empty only
#    checks == 0.0, so NaN/inf slip past it).
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "bad",
    [
        [float("nan"), float("nan")],
        [float("inf"), 0.1],
        [0.1, float("-inf")],
        [float("nan"), 0.0],
        [float("inf"), float("inf")],
    ],
)
def test_pinecone_nan_inf_must_fail_closed(bad):
    kind, payload = _assert_fail_closed(
        lambda: embed_texts(["a"], embedding_model="m", pinecone_client=_pinecone([bad])),
        n_inputs=1,
    )
    # Document the outcome explicitly: a clean return here is a BREACH and the
    # assertion inside _assert_fail_closed will already have fired.
    assert kind in ("raised", "returned")


@pytest.mark.parametrize(
    "bad",
    [
        [float("nan"), 0.5],
        [float("inf"), float("inf")],
    ],
)
def test_litellm_nan_inf_must_fail_closed(monkeypatch, bad):
    class _R:
        data = [{"embedding": bad}]

    _install_litellm(monkeypatch, lambda **kw: _R())
    _assert_fail_closed(
        lambda: embed_texts(["a"], embedding_model="text-embedding-3-small"),
        n_inputs=1,
    )


# ---------------------------------------------------------------------------
# 2. zero / empty vectors (single + mixed-with-good).
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "vecs,n",
    [
        ([[0.0, 0.0]], 1),
        ([[]], 1),
        ([[0.1, 0.2], [0.0, 0.0]], 2),       # one good, one zero
        ([[0.1, 0.2], []], 2),               # one good, one empty
        ([[0.0], [0.0]], 2),
        ([[0.0, 0.0, 0.0, 0.0, 0.0]], 1),
    ],
)
def test_pinecone_zero_empty_fail_closed(vecs, n):
    kind, _ = _assert_fail_closed(
        lambda: embed_texts(
            [f"t{i}" for i in range(n)], embedding_model="m", pinecone_client=_pinecone(vecs)
        ),
        n_inputs=n,
    )
    assert kind == "raised", "zero/empty vector must raise, not return"


# ---------------------------------------------------------------------------
# 3. mismatched counts: N vectors for M inputs (both directions).
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "n_vecs,n_inputs",
    [
        (1, 2), (2, 1), (0, 3), (3, 0), (5, 2), (2, 5), (1, 50), (50, 1),
    ],
)
def test_pinecone_count_mismatch_fail_closed(n_vecs, n_inputs):
    vecs = [[0.1, 0.2] for _ in range(n_vecs)]
    inputs = [f"t{i}" for i in range(n_inputs)]
    if n_inputs == 0:
        # empty input short-circuits to [] BEFORE the provider is called;
        # that is a legitimate clean empty return, not a breach.
        assert embed_texts(inputs, embedding_model="m", pinecone_client=_pinecone(vecs)) == []
        return
    kind, _ = _assert_fail_closed(
        lambda: embed_texts(inputs, embedding_model="m", pinecone_client=_pinecone(vecs)),
        n_inputs=n_inputs,
    )
    assert kind == "raised", "count mismatch must raise"


def test_litellm_count_mismatch_fewer_vectors(monkeypatch):
    # litellm route indexes resp.data[i] for i in range(len(items)); fewer
    # vectors than inputs must NOT silently return a short list.
    class _R:
        data = [{"embedding": [0.1, 0.2]}]  # 1 vector

    _install_litellm(monkeypatch, lambda **kw: _R())
    kind, _ = _assert_fail_closed(
        lambda: embed_texts(["a", "b", "c"], embedding_model="m"),
        n_inputs=3,
    )
    assert kind == "raised"


def test_litellm_count_mismatch_more_vectors(monkeypatch):
    # More vectors than inputs: code only reads the first len(items); the
    # extras are dropped but len matches => could it return a wrong mapping?
    class _R:
        data = [{"embedding": [0.1]}, {"embedding": [0.2]}, {"embedding": [0.3]}]

    _install_litellm(monkeypatch, lambda **kw: _R())
    _assert_fail_closed(
        lambda: embed_texts(["a", "b"], embedding_model="m"),
        n_inputs=2,
    )


# ---------------------------------------------------------------------------
# 4. dimension mismatch.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "vecs,exp_dim,n",
    [
        ([[0.1, 0.2, 0.3]], 2, 1),
        ([[0.1]], 2, 1),
        ([[0.1, 0.2], [0.1, 0.2, 0.3]], 2, 2),   # ragged
        ([[0.1, 0.2, 0.3, 0.4]], 1024, 1),
    ],
)
def test_pinecone_dim_mismatch_fail_closed(vecs, exp_dim, n):
    kind, _ = _assert_fail_closed(
        lambda: embed_texts(
            [f"t{i}" for i in range(n)],
            embedding_model="m",
            pinecone_client=_pinecone(vecs),
            expected_dim=exp_dim,
        ),
        n_inputs=n,
        expected_dim=exp_dim,
    )
    assert kind == "raised", "dim mismatch must raise"


# ---------------------------------------------------------------------------
# 5. huge batch (10k texts) round-trips cleanly with valid vectors.
# ---------------------------------------------------------------------------
def test_huge_batch_pinecone_ok():
    n = 10_000
    vecs = [[0.01, 0.02, 0.03] for _ in range(n)]
    out = embed_texts(
        [f"doc-{i}" for i in range(n)], embedding_model="m", pinecone_client=_pinecone(vecs)
    )
    assert len(out) == n
    assert all(not _is_garbage_vector(v) for v in out[:100] + out[-100:])


def test_huge_batch_one_bad_in_the_middle_fails_closed():
    n = 10_000
    vecs = [[0.5, 0.5] for _ in range(n)]
    vecs[5000] = [0.0, 0.0]  # one poisoned vector hidden in a huge batch
    _assert_fail_closed(
        lambda: embed_texts(
            [f"d{i}" for i in range(n)], embedding_model="m", pinecone_client=_pinecone(vecs)
        ),
        n_inputs=n,
    )


# ---------------------------------------------------------------------------
# 6. unicode / empty / None-ish texts as INPUT (valid vectors returned).
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "texts",
    [
        ["", "", ""],
        ["\x00\x00", "﻿", "‮"],          # nulls, BOM, RTL override
        ["😀🔥💀", "naïve café", "日本語テスト"],
        ["a" * 100_000],                            # very long single text
        [None, 123, 4.5, True],                     # None-ish / non-str inputs
        ["\n\t\r ", "   "],                          # whitespace-only
    ],
)
def test_unicode_noneish_inputs_clean_vectors(texts):
    n = len(texts)
    vecs = [[0.1, 0.2] for _ in range(n)]
    out = embed_texts(texts, embedding_model="m", pinecone_client=_pinecone(vecs))
    assert len(out) == n
    assert all(not _is_garbage_vector(v) for v in out)


def test_only_empty_string_inputs_still_embedded():
    # Empty STRINGS are non-empty list items -> provider IS called (unlike []).
    out = embed_texts(["", ""], embedding_model="m", pinecone_client=_pinecone([[0.3], [0.4]]))
    assert out == [[0.3], [0.4]]


# ---------------------------------------------------------------------------
# 7. provider raises: Auth / RateLimit / Timeout / generic must propagate
#    (re-raised, NEVER swallowed to a fallback vector).
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "exc",
    [
        _AuthenticationError("bad key"),
        _RateLimitError("429"),
        _TimeoutError("timed out"),
        RuntimeError("boom"),
        ValueError("malformed"),
        KeyError("embedding"),
        ConnectionError("reset"),
    ],
)
def test_litellm_provider_error_reraised(monkeypatch, exc):
    def _raise(**kw):
        raise exc

    _install_litellm(monkeypatch, _raise)
    kind, got = _assert_fail_closed(
        lambda: embed_texts(["a"], embedding_model="m"),
        n_inputs=1,
    )
    assert kind == "raised", "provider error must propagate, not be swallowed"
    assert got is exc or type(got) is type(exc)


@pytest.mark.parametrize(
    "exc",
    [
        _AuthenticationError("bad pinecone key"),
        _RateLimitError("429"),
        _TimeoutError("timed out"),
        RuntimeError("boom"),
    ],
)
def test_pinecone_provider_error_reraised(exc):
    class _RaisingInference:
        def embed(self, model, inputs, parameters):
            raise exc

    class _RaisingClient:
        inference = _RaisingInference()

    kind, got = _assert_fail_closed(
        lambda: embed_texts(["a"], embedding_model="m", pinecone_client=_RaisingClient()),
        n_inputs=1,
    )
    assert kind == "raised"
    assert type(got) is type(exc)


# ---------------------------------------------------------------------------
# 8. malformed provider RESPONSES (not exceptions, just junk shapes).
# ---------------------------------------------------------------------------
def test_pinecone_data_item_missing_values_fail_closed():
    # data item with neither .values nor .get -> code appends [] -> must raise.
    class _Bare:
        pass

    class _Resp:
        data = [_Bare()]

    class _Inf:
        def embed(self, **kw):
            return _Resp()

        # signature compat
        def __init__(self):
            pass

    class _Client:
        def __init__(self):
            self.inference = _InfWrap()

    class _InfWrap:
        def embed(self, model, inputs, parameters):
            return _Resp()

    kind, _ = _assert_fail_closed(
        lambda: embed_texts(["a"], embedding_model="m", pinecone_client=_Client()),
        n_inputs=1,
    )
    assert kind == "raised", "missing .values -> empty vector -> must raise"


def test_pinecone_values_none_fail_closed():
    class _D:
        values = None

    class _Resp:
        data = [_D()]

    class _Inf:
        def embed(self, model, inputs, parameters):
            return _Resp()

    class _Client:
        inference = _Inf()

    kind, _ = _assert_fail_closed(
        lambda: embed_texts(["a"], embedding_model="m", pinecone_client=_Client()),
        n_inputs=1,
    )
    assert kind == "raised"


def test_litellm_missing_embedding_key_propagates(monkeypatch):
    class _R:
        data = [{"not_embedding": [0.1, 0.2]}]  # wrong key

    _install_litellm(monkeypatch, lambda **kw: _R())
    kind, _ = _assert_fail_closed(
        lambda: embed_texts(["a"], embedding_model="m"),
        n_inputs=1,
    )
    assert kind == "raised", "missing 'embedding' key must raise (KeyError), not return"


def test_litellm_data_too_short_propagates(monkeypatch):
    class _R:
        data = []  # zero vectors, indexing range(len(items)) -> IndexError

    _install_litellm(monkeypatch, lambda **kw: _R())
    kind, _ = _assert_fail_closed(
        lambda: embed_texts(["a", "b"], embedding_model="m"),
        n_inputs=2,
    )
    assert kind == "raised"


# ---------------------------------------------------------------------------
# 9. randomized fuzz: hammer with random shapes/payloads, assert the
#    contract holds for EVERY single iteration (bounded, deterministic seed).
# ---------------------------------------------------------------------------
def _random_vector(rng, dim):
    choice = rng.random()
    if choice < 0.15:
        return [0.0] * dim                       # zero
    if choice < 0.25:
        return []                                # empty
    if choice < 0.35:
        return [float("nan")] * dim              # nan garbage
    if choice < 0.45:
        return [float("inf")] + [0.1] * (dim - 1) if dim else [float("inf")]
    if choice < 0.55:
        return [rng.uniform(-1, 1) for _ in range(rng.randint(1, dim + 3))]  # ragged
    return [rng.uniform(-1, 1) or 0.01 for _ in range(dim)]  # mostly-valid


def test_random_fuzz_pinecone_contract_holds():
    rng = random.Random(1337)
    breaches = []
    for it in range(2000):
        n_inputs = rng.randint(1, 6)
        n_vecs = rng.choice([n_inputs, n_inputs, rng.randint(0, 8)])
        dim = rng.randint(1, 8)
        exp_dim = rng.choice([None, dim, dim + 1])
        vecs = [_random_vector(rng, dim) for _ in range(n_vecs)]
        inputs = [f"t{i}-{rng.random()}" for i in range(n_inputs)]
        try:
            out = embed_texts(
                inputs, embedding_model="m", pinecone_client=_pinecone(vecs),
                expected_dim=exp_dim,
            )
        except Exception:
            continue  # any raise = fail-closed OK
        # Returned without raising: validate hard.
        if len(out) != n_inputs:
            breaches.append((it, "count", len(out), n_inputs, vecs))
            continue
        for v in out:
            if _is_garbage_vector(v, exp_dim):
                breaches.append((it, "garbage", v, exp_dim, vecs))
                break
    assert not breaches, f"FAIL-CLOSED BREACHES ({len(breaches)}): {breaches[:5]}"


def test_random_fuzz_litellm_contract_holds(monkeypatch):
    rng = random.Random(4242)
    state = {"vecs": []}

    def _embedding(**kw):
        class _R:
            data = [{"embedding": v} for v in state["vecs"]]
        return _R()

    _install_litellm(monkeypatch, _embedding)
    breaches = []
    for it in range(2000):
        n_inputs = rng.randint(1, 6)
        n_vecs = rng.choice([n_inputs, rng.randint(0, 8)])
        dim = rng.randint(1, 8)
        exp_dim = rng.choice([None, dim, dim + 1])
        state["vecs"] = [_random_vector(rng, dim) for _ in range(n_vecs)]
        inputs = [f"t{i}" for i in range(n_inputs)]
        try:
            out = embed_texts(
                inputs, embedding_model="m", provider_api_key="sk-x", expected_dim=exp_dim
            )
        except Exception:
            continue
        if len(out) != n_inputs:
            breaches.append((it, "count", len(out), n_inputs))
            continue
        for v in out:
            if _is_garbage_vector(v, exp_dim):
                breaches.append((it, "garbage", v, exp_dim))
                break
    assert not breaches, f"FAIL-CLOSED BREACHES ({len(breaches)}): {breaches[:5]}"
