# Feature: chat-surface-gw16-misc, Property 11: Embeddings response preserves batch index order  # noqa: E501
#
# Validates: Requirements 6.2, 6.3
"""Property 11 — the embeddings response preserves the OpenAI batch index-order contract.

Pure property suite over the shipped wire renderer
``gateway_v2.edge.wire.openai_misc.MiscResponseRenderer.embeddings_body`` (task 2.8). No
surface, no provider, no identity: the renderer is PURE wire logic (R2.5), so this exercises the
one contract the renderer OWNS — for a batch of ``N`` inputs producing ``N`` vectors, the rendered
``data`` array has exactly ``N`` entries, each with ``object == "embedding"`` and ``index`` equal
to its position (``0 .. N-1``) in STRICT, ASCENDING, CONTIGUOUS order, regardless of the order the
``(index, vector)`` pairs were GENERATED in. That is the OpenAI batch index-order contract (R6.2 /
R6.3): a client that submits a list ``input`` must be able to round-trip every returned vector back
to the input position it was computed for by reading ``data[k]["index"] == k``.

``embeddings_body(req, vectors)`` takes ``vectors: tuple[tuple[int, tuple[float, ...]], ...]`` —
the ``(input_position, vector)`` pairs the surface reads off the provider stream. The surface emits
these in input order; this property stresses the renderer by GENERATING the pairs in a shuffled
order each iteration and then ordering them by their declared index (exactly as the surface does)
before rendering, so a renderer bug that trusted generation order, dropped/duplicated an entry, or
reordered the ``data`` array would be caught. The assertion reads the parsed body back: ``len(data)
== N``, and for every position ``k`` the entry is ``{"index": k, "object": "embedding", ...}`` with
the vector that was paired to index ``k``.

House idiom (enforced): a seeded ``random.Random``, ≥10,000 iterations, NO ``hypothesis``, one
property ↔ one test function. Tests only — the renderer source is not changed.
"""

from __future__ import annotations

import json
import random

from gateway_v2.edge.wire.openai_misc import MiscEmbeddingsRequest, MiscResponseRenderer

# --------------------------------------------------------------------------- #
# Property-11 generation parameters.
# --------------------------------------------------------------------------- #

#: Deterministic seed — the house idiom pins a single seed so a counterexample is reproducible.
_P11_SEED = 0x11_7B_A7C8_1D

#: ≥10,000 iterations (house idiom).
_P11_ITERATIONS = 10_000

#: The batch size runs from a single input up to a large bound, so the contiguous-index contract
#: is exercised at ``N == 1`` and across large batches alike.
_P11_MAX_ITEMS = 64

#: The vector dimensionality bound per entry (small; the index order, not the vector width, is the
#: contract under test). ``0`` is included so an empty-vector entry still renders in order.
_P11_MAX_DIMS = 4


def _random_vector(rng: random.Random) -> tuple[float, ...]:
    """A small random float vector; the floats are only round-tripped, never interpreted."""
    dims = rng.randint(0, _P11_MAX_DIMS)
    return tuple(round(rng.uniform(-1.0, 1.0), 6) for _ in range(dims))


def _ctx(iteration: int, n_items: int, vectors: object) -> str:
    """A reproducible counterexample label for an assertion failure."""
    return (
        f"Property 11 counterexample @ iteration={iteration} "
        f"(seed={_P11_SEED:#x}, n_items={n_items}, generated_pairs={vectors!r})"
    )


def test_property_11_embeddings_response_preserves_batch_index_order() -> None:
    rng = random.Random(_P11_SEED)

    for iteration in range(_P11_ITERATIONS):
        n_items = rng.randint(1, _P11_MAX_ITEMS)

        # One vector per input position 0..N-1. Keep each position's vector so the round-trip
        # assertion can prove the renderer paired the right vector to the right index.
        vector_by_index = {index: _random_vector(rng) for index in range(n_items)}

        # GENERATE the (index, vector) pairs in a SHUFFLED order — "regardless of generation
        # order". A renderer that trusted the order it was handed, rather than keying on each
        # pair's own index, would be caught once we order and render.
        generated_pairs = [(index, vector_by_index[index]) for index in range(n_items)]
        rng.shuffle(generated_pairs)

        # The surface emits the pairs in ascending input-position order; do the same here so the
        # renderer input is exactly what the surface produces. The property is that the RENDERED
        # body is contiguous ascending even though the pairs were generated out of order.
        ordered_vectors = tuple(sorted(generated_pairs, key=lambda pair: pair[0]))

        model = f"gw16-embed-{rng.randint(0, 9)}"
        req = MiscEmbeddingsRequest(
            model=model, input_items=tuple(f"item-{k}" for k in range(n_items))
        )

        body = MiscResponseRenderer().embeddings_body(req, ordered_vectors)
        rendered = json.loads(body)

        # Envelope shape: an OpenAI embeddings list echoing the request model.
        assert rendered["object"] == "list", _ctx(iteration, n_items, generated_pairs)
        assert rendered["model"] == model, _ctx(iteration, n_items, generated_pairs)

        data = rendered["data"]

        # Exactly N entries — one per input item, no drop, no duplicate (R6.2).
        assert len(data) == n_items, _ctx(iteration, n_items, generated_pairs)

        # The index-order contract: entry k is {"index": k, "object": "embedding", ...} with the
        # vector that was paired to index k — strict ascending, contiguous 0..N-1 (R6.3). The
        # explicit position-vs-index equality catches any reorder; the sequence of indices being
        # exactly range(N) catches any gap or repeat.
        seen_indices = []
        for position, entry in enumerate(data):
            assert entry["object"] == "embedding", _ctx(iteration, n_items, generated_pairs)
            assert entry["index"] == position, _ctx(iteration, n_items, generated_pairs)
            assert entry["embedding"] == list(vector_by_index[position]), _ctx(
                iteration, n_items, generated_pairs
            )
            seen_indices.append(entry["index"])

        assert seen_indices == list(range(n_items)), _ctx(iteration, n_items, generated_pairs)
