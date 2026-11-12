"""F1a-2 evidence: the forward-compatible stamp (step 1) against HEAD's stamp code.

Run from `gateway_v2/` with the gateway venv:

    ../../venv-ci/bin/python -I ../docs/plans/evidence/2026-11-11-f1a2-model-state/stamp_compat.py

`-I` is fine: the script puts the current directory (the worktree's gateway_v2) on sys.path itself.

What it proves, against the code at the worktree's HEAD (`runtime/state_sig.py`,
`state_control/valkey.py`):

1. Signing over the RAW cursor map (kind names as strings, sorted) is byte-identical to HEAD's
   `stamp_signature` for every stamp HEAD can mint. So a step-1 reader accepts every stamp a
   step-0 (HEAD) re-hydrator writes, and a step-0 reader accepts every stamp a step-1
   re-hydrator writes while the floor names only the four base kinds.
2. A stamp that names a fifth kind (`model`) verifies under the step-1 reader, which ignores the
   kind it does not know, and is REFUSED by HEAD's `decode_stamp` ("unknown kind"). This is why
   step 1 must be on every reader before any re-hydrator names MODEL.
3. HEAD's held-stamp comparison (`ValkeyPublisher._stamp_is_newer`) treats such a stamp as
   unverifiable and OVERWRITES it even when it is newer: the stored stamp time moves backwards.
   The step-1 comparison keeps the newer stamp.
4. The step-1 reader still refuses: a forged signature, a stamp missing a REQUIRED kind, a
   malformed kind name, a non-integer cursor.

The step-1 functions here are a prototype of the design in the plan, not product code.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import random
import re
import sys
import time
from collections.abc import Mapping

sys.path.insert(0, ".")

from gateway_v2.domain.state import Cursor, StateKind, StoreDataUnavailable, Version  # noqa: E402
from gateway_v2.runtime.state_sig import (  # noqa: E402
    STAMP_DOMAIN,
    decode_stamp,
    encode_stamp,
    make_stamp,
    stamp_signature,
)
from state_control.valkey import ValkeyPublisher  # noqa: E402

SECRET = b"f1a2-evidence-not-a-real-key"
KIND_NAME = re.compile(r"[a-z][a-z0-9_]{0,31}")
BASE = ("budget", "key", "ks", "plan")


# --- step-1 prototype ---------------------------------------------------------------------------


def raw_signature(
    secret: bytes,
    verified_at_ms: int,
    cursors: Mapping[str, tuple[int, int, int]],
    by: str,
    *,
    deep: bool,
    degraded: bool,
) -> str:
    """HEAD's layout, with kind NAMES instead of enum members. Same bytes for the same set."""
    parts = [
        STAMP_DOMAIN,
        str(verified_at_ms),
        by,
        str(int(deep)),
        str(int(degraded)),
        str(len(cursors)),
    ]
    for name in sorted(cursors):
        epoch, seq, feed = cursors[name]
        parts.append(f"{name}={epoch}.{seq}:{feed}")
    return hmac.new(secret, "\n".join(parts).encode("utf-8"), hashlib.sha256).hexdigest()


def lenient_decode(
    secret: bytes, raw: bytes, *, required: frozenset[str],
) -> tuple[float, dict[StateKind, Cursor], frozenset[str]]:
    """Verify over the raw map; return (verified_at, KNOWN cursors, every name it covered).

    Refuses on: malformed envelope, malformed name or cursor, bad signature, or a REQUIRED kind
    missing. Unknown names are verified (they are inside the signature) and then ignored.
    """
    env = json.loads(raw)
    if not isinstance(env, dict):
        raise StoreDataUnavailable("not an object")
    ms = env.get("verified_at_ms")
    if isinstance(ms, bool) or not isinstance(ms, int) or ms < 0:
        raise StoreDataUnavailable("verified_at_ms")
    cursors_raw = env.get("cursors")
    if not isinstance(cursors_raw, dict):
        raise StoreDataUnavailable("cursors")
    cursors: dict[str, tuple[int, int, int]] = {}
    for name, triple in cursors_raw.items():
        if not isinstance(name, str) or not KIND_NAME.fullmatch(name):
            raise StoreDataUnavailable(f"bad kind name {name!r}")
        if not isinstance(triple, list) or len(triple) != 3 or any(
            isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in triple
        ):
            raise StoreDataUnavailable(f"bad cursor for {name}")
        cursors[name] = (triple[0], triple[1], triple[2])
    by, deep, degraded, sig = env.get("by"), env.get("deep"), env.get("degraded"), env.get("sig")
    if not isinstance(by, str) or not isinstance(deep, bool) or not isinstance(degraded, bool):
        raise StoreDataUnavailable("fields")
    if not isinstance(sig, str):
        raise StoreDataUnavailable("sig")
    expected = raw_signature(secret, ms, cursors, by, deep=deep, degraded=degraded)
    if not hmac.compare_digest(sig, expected):
        raise StoreDataUnavailable("signature")
    missing = sorted(required - set(cursors))
    if missing:
        raise StoreDataUnavailable(f"does not cover required {missing}")
    known: dict[StateKind, Cursor] = {}
    for name, (epoch, seq, feed) in cursors.items():
        try:
            kind = StateKind(name)
        except ValueError:
            continue  # verified, then ignored: a kind this build does not apply
        known[kind] = Cursor(Version(epoch, seq), feed)
    return ms / 1000, known, frozenset(cursors)


def raw_stamp(ms: int, cursors: Mapping[str, tuple[int, int, int]], by: str) -> bytes:
    sig = raw_signature(SECRET, ms, cursors, by, deep=False, degraded=False)
    env = {
        "verified_at_ms": ms,
        "cursors": {k: list(v) for k, v in sorted(cursors.items())},
        "by": by,
        "deep": False,
        "degraded": False,
        "sig": sig,
    }
    return json.dumps(env, separators=(",", ":")).encode()


# --- checks -------------------------------------------------------------------------------------


def main() -> int:
    seed = 20261111
    rng = random.Random(seed)
    n = 5000
    print(f"seed={seed} iterations={n} python={sys.version.split()[0]}")

    # 1. byte-identical signatures for every HEAD-mintable stamp
    for _ in range(n):
        cursors = {
            kind: Cursor(Version(rng.randint(1, 9), rng.randint(0, 10**6)), rng.randint(0, 10**9))
            for kind in StateKind
        }
        stamp = make_stamp(
            SECRET, rng.uniform(1.7e9, 1.8e9), cursors, f"rh-{rng.randint(0, 99)}:{rng.randint(1, 9999)}",
            deep=rng.random() < 0.1, degraded=rng.random() < 0.1,
        )
        raw = encode_stamp(stamp)
        as_raw = {k.value: (c.version.epoch, c.version.seq, c.feed_seq) for k, c in cursors.items()}
        ms = int(stamp.verified_at * 1000)
        assert raw_signature(
            SECRET, ms, as_raw, stamp.by, deep=stamp.deep, degraded=stamp.degraded,
        ) == stamp_signature(SECRET, ms, cursors, stamp.by, deep=stamp.deep, degraded=stamp.degraded)
        verified_at, known, covered = lenient_decode(SECRET, raw, required=frozenset(BASE))
        assert known == dict(cursors) and covered == frozenset(BASE)
    print(f"[1] {n}/{n} HEAD stamps: raw-map signature byte-identical; step-1 reader accepts all")

    # 1b. a step-1 writer naming only the base kinds is accepted by HEAD's reader
    for _ in range(n):
        cur = {k: (1, rng.randint(0, 99), rng.randint(0, 999)) for k in BASE}
        raw = raw_stamp(rng.randint(1, 2**41), cur, "rh-step1")
        decode_stamp(SECRET, raw)
    print(f"[1b] {n}/{n} step-1 stamps over the base kinds: HEAD decode_stamp accepts all")

    # 2. a stamp naming MODEL: step-1 accepts (ignores), HEAD refuses
    refused = 0
    for _ in range(n):
        cur = {k: (1, rng.randint(0, 99), rng.randint(0, 999)) for k in (*BASE, "model")}
        raw = raw_stamp(rng.randint(1, 2**41), cur, "rh-step2")
        _, known, covered = lenient_decode(SECRET, raw, required=frozenset(BASE))
        assert "model" in covered and all(k.value in BASE for k in known)
        try:
            decode_stamp(SECRET, raw)
        except StoreDataUnavailable as exc:
            assert "unknown kind" in str(exc), exc
            refused += 1
    print(f"[2] {n}/{n} stamps naming 'model': step-1 accepts (model ignored); "
          f"HEAD decode_stamp refuses {refused}/{n} ('unknown kind')")

    # 3. HEAD's held-stamp comparison overwrites a NEWER stamp it cannot decode
    pub = ValkeyPublisher(client=None, secret=SECRET)
    newer_ms = 1_800_000_000_500
    held = raw_stamp(newer_ms, {k: (1, 5, 50) for k in (*BASE, "model")}, "rh-step2")
    older = make_stamp(SECRET, (newer_ms - 400) / 1000, {
        k: Cursor(Version(1, 5), 50) for k in StateKind}, "rh-step0")
    head_keeps_newer = pub._stamp_is_newer(held, older)
    held_at, _, _ = lenient_decode(SECRET, held, required=frozenset())
    step1_keeps_newer = held_at >= older.verified_at
    print(f"[3] held stamp at {newer_ms} ms names 'model'; a round that started 400 ms earlier: "
          f"HEAD _stamp_is_newer={head_keeps_newer} (False = overwrite: stamp time moves BACK "
          f"400 ms); step-1 comparison keeps the newer stamp={step1_keeps_newer}")
    assert head_keeps_newer is False and step1_keeps_newer is True

    # 4. step-1 still refuses what it must
    good = {k: (1, 2, 3) for k in (*BASE, "model")}
    raw = raw_stamp(1_000, good, "rh")
    env = json.loads(raw)
    cases = {
        "forged signature": dict(env, sig="0" * 64),
        "required kind missing": None,
        "newline in a kind name": dict(env, cursors={**env["cursors"], "mo\ndel": [1, 2, 3]}),
        "'=' in a kind name": dict(env, cursors={**env["cursors"], "a=1.2:3": [1, 2, 3]}),
        "bool cursor": dict(env, cursors={**env["cursors"], "model": [True, 2, 3]}),
        "negative cursor": dict(env, cursors={**env["cursors"], "model": [1, -2, 3]}),
    }
    for label, mutated in cases.items():
        if mutated is None:
            payload = raw_stamp(1_000, {k: (1, 2, 3) for k in BASE}, "rh")
            required = frozenset((*BASE, "model"))
        else:
            payload = json.dumps(mutated).encode()
            required = frozenset(BASE)
        try:
            lenient_decode(SECRET, payload, required=required)
        except StoreDataUnavailable as exc:
            print(f"[4] refused: {label}: {exc}")
        else:
            raise AssertionError(f"accepted: {label}")

    # cost: step-1 decode vs HEAD decode (thread CPU)
    raw_head = encode_stamp(make_stamp(SECRET, 1.7e9, {k: Cursor(Version(1, 2), 3) for k in StateKind}, "rh"))
    for label, fn in (
        ("HEAD decode_stamp", lambda: decode_stamp(SECRET, raw_head)),
        ("step-1 lenient_decode", lambda: lenient_decode(SECRET, raw_head, required=frozenset(BASE))),
    ):
        t0 = time.thread_time()
        for _ in range(20000):
            fn()
        print(f"[cost] {label}: {(time.thread_time() - t0) / 20000 * 1e6:.1f} us thread CPU per stamp")
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
