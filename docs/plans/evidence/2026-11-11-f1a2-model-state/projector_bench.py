"""F1a-2 evidence: the MODEL projection's CPU per round at 100k orgs x 5 models.

Run from `gateway_v2/` (HEAD's canonical_body / body_hash are used for the bodies):

    flock /home/claude/work/.heavy.lock nice -n 10 ../../venv-ci/bin/python -I \
        ../docs/plans/evidence/2026-11-11-f1a2-model-state/projector_bench.py

A PROTOTYPE of the plan's derivation (`derive_row`, `assemble`), with every row wrapped in
`except Exception` (one bad row never stops the round). It measures, in thread CPU:

* cold: derive all 500k rows and assemble 100k bodies (projector start, empty memo);
* steady: nothing changed; compare 100k per-org source revisions with the memo;
* churn: 1% of orgs changed (one row each), re-derive those orgs (memo hits for the rest of
  their rows) and assemble;
* breaker: count per-reason newly-omitted entries for a round;
* a poison row (nesting-depth bomb in a field, a RecursionError raiser) inside the estate.

It also measures the memo's memory at 20k orgs (tracemalloc) and states the linear extrapolation.
Credential encryption is F1a-3's: here the `cred` slot is `{"t": "pending"}`.
"""

from __future__ import annotations

import gc
import hashlib
import random
import re
import sys
import time
import tracemalloc
from dataclasses import dataclass

sys.path.insert(0, ".")

from gateway_v2.runtime.state_sig import body_hash, canonical_body  # noqa: E402

PROVIDERS = ("openai", "anthropic", "azure", "google", "aws_bedrock", "mistral", "cohere",
             "deepseek", "huggingface", "meta", "ollama", "custom")
ALIAS = re.compile(r"[A-Za-z0-9._:/-]{1,128}")
UPSTREAM = re.compile(r"[A-Za-z0-9._:/@+-]{1,256}")
LITELLM_PREFIX = {
    "openai": ("openai/",), "anthropic": ("anthropic/",), "azure": ("azure/",),
    "google": ("gemini/",), "aws_bedrock": ("bedrock/",), "mistral": ("mistral/",),
    "cohere": ("cohere/", "cohere_chat/"), "deepseek": ("deepseek/",),
    "huggingface": ("huggingface/",), "meta": ("meta_llama/",), "ollama": ("ollama/", "ollama_chat/"),
    "custom": (),
}
FERNET = re.compile(r"gAAAAA[A-Za-z0-9_-]{50,8000}={0,2}")
SENS = ("public", "internal", "confidential", "restricted")
MAX_MODELS = 64


@dataclass(frozen=True, slots=True)
class Row:
    row_id: int
    org_id: int
    provider: str
    model_name: str
    model_id: str
    api_base: str
    region: str
    is_active: bool
    token: str
    env_var: str
    sensitivity: str
    tags: tuple[str, ...]
    created: int


def row_digest(r: Row) -> bytes:
    h = hashlib.sha256()
    for part in (str(r.row_id), r.provider, r.model_name, r.model_id, r.api_base, r.region,
                 "1" if r.is_active else "0", r.token, r.env_var, r.sensitivity,
                 ",".join(r.tags), str(r.created)):
        h.update(part.encode("utf-8", "surrogatepass"))
        h.update(b"\x1f")
    return h.digest()


def derive_row(r: Row) -> tuple[str, dict[str, object] | str]:
    """(alias key, entry) or (alias key, omission reason)."""
    key = r.model_name.casefold()
    if not r.is_active:
        return key, "inactive"
    if r.provider not in LITELLM_PREFIX:
        return key, "unknown_provider"
    if not ALIAS.fullmatch(r.model_name):
        return key, "alias_invalid"
    upstream = r.model_id
    for prefix in LITELLM_PREFIX[r.provider]:
        if upstream.startswith(prefix):
            upstream = upstream[len(prefix):]
            break
    if not UPSTREAM.fullmatch(upstream):
        return key, "upstream_invalid"
    if not r.token:
        return key, "credential_env_var" if r.env_var else "credential_missing"
    if not FERNET.fullmatch(r.token):
        return key, "credential_invalid_format"
    return key, {
        "key": key, "alias": r.model_name, "row": r.row_id, "provider": r.provider,
        "upstream": upstream, "base_url": r.api_base or None, "region": r.region or None,
        "created": r.created, "tags": sorted(r.tags), "sensitivity": r.sensitivity,
        "cred": {"t": "pending"},
    }


def assemble(slug: str, rows: list[Row], memo: dict[int, tuple[bytes, tuple[str, object]]]) -> tuple[dict[str, object], dict[str, int]]:
    served: dict[str, dict[str, object]] = {}
    omitted: dict[str, str] = {}
    reasons: dict[str, int] = {}
    seen: dict[str, int] = {}
    for r in rows:
        try:
            digest = row_digest(r)
            hit = memo.get(r.row_id)
            if hit is not None and hit[0] == digest:
                key, value = hit[1]
            else:
                key, value = derive_row(r)
                memo[r.row_id] = (digest, (key, value))
        except Exception:  # noqa: BLE001 - one bad row never stops the round
            key, value = f"row-{r.row_id}", "row_invalid"
        seen[key] = seen.get(key, 0) + 1
        if isinstance(value, str):
            omitted[key] = value
        else:
            served[key] = value  # type: ignore[assignment]
    for key, n in seen.items():
        if n > 1:  # case-fold collision: every colliding row is omitted
            served.pop(key, None)
            omitted[key] = "alias_collision"
    keys = sorted(served)
    if len(keys) > MAX_MODELS:
        for key in keys[MAX_MODELS:]:
            served.pop(key)
            omitted[key] = "org_model_cap"
    for v in omitted.values():
        reasons[v] = reasons.get(v, 0) + 1
    body = {
        "schema": 1, "org_id": slug,
        "models": [served[k] for k in sorted(served)],
        "omitted": [{"key": k, "alias": k, "reason": omitted[k]} for k in sorted(omitted)][:64],
        "omitted_overflow": max(len(omitted) - 64, 0),
        "allowed": None, "default": None,
    }
    return body, reasons


def estate(n_orgs: int, per_org: int, rng: random.Random) -> dict[int, list[Row]]:
    out: dict[int, list[Row]] = {}
    rid = 0
    for org in range(n_orgs):
        rows = []
        for i in range(per_org):
            rid += 1
            p = PROVIDERS[(org + i) % len(PROVIDERS)]
            prefix = LITELLM_PREFIX[p][0] if LITELLM_PREFIX[p] else ""
            rows.append(Row(
                rid, org, p, f"model-{i}", f"{prefix}model-{i}-2024", "", "", True,
                "gAAAAA" + "x" * 130, "", SENS[i % 4], ("SOC2",), 1_731_300_000 + rid,
            ))
        out[org] = rows
    return out


def main() -> int:
    rng = random.Random(20261111)
    n_orgs, per_org = 100_000, 5
    print(f"python={sys.version.split()[0]} orgs={n_orgs:,} models/org={per_org} (thread CPU)")
    rows = estate(n_orgs, per_org, rng)
    slugs = {org: f"org-{org:06d}" for org in rows}
    # a poison row: a deeply nested structure in a field that a careless derivation recursed into
    class Poison(str):
        def casefold(self) -> str:  # type: ignore[override]
            raise RecursionError("maximum recursion depth exceeded")
    rows[123][0] = Row(**{**{f: getattr(rows[123][0], f) for f in Row.__slots__}, "model_name": Poison("(" * 499)})  # type: ignore[arg-type]

    memo: dict[int, tuple[bytes, tuple[str, object]]] = {}
    written: dict[int, str] = {}
    gc.collect()
    t0 = time.thread_time()
    totals: dict[str, int] = {}
    for org, org_rows in rows.items():
        body, reasons = assemble(slugs[org], org_rows, memo)
        written[org] = body_hash(canonical_body(body))
        for k, v in reasons.items():
            totals[k] = totals.get(k, 0) + v
    cold = time.thread_time() - t0
    print(f"cold round (derive 500k rows, assemble+canonicalise+hash 100k bodies): {cold:.2f} s; "
          f"omissions={totals}")

    revs = {org: 1 for org in rows}
    memo_revs = dict(revs)
    t0 = time.thread_time()
    changed = [org for org, rev in revs.items() if memo_revs.get(org) != rev]
    steady = time.thread_time() - t0
    print(f"steady round (compare 100k source revisions, nothing changed): {steady * 1000:.1f} ms; "
          f"changed={len(changed)}")

    churn = rng.sample(sorted(rows), n_orgs // 100)
    for org in churn:
        old = rows[org][0]
        rows[org][0] = Row(**{**{f: getattr(old, f) for f in Row.__slots__}, "model_id": old.model_id + "-v2"})
        revs[org] += 1
    t0 = time.thread_time()
    changed = [org for org, rev in revs.items() if memo_revs.get(org) != rev]
    writes = 0
    for org in changed:
        body, _ = assemble(slugs[org], rows[org], memo)
        digest = body_hash(canonical_body(body))
        if digest != written[org]:
            written[org] = digest
            writes += 1
        memo_revs[org] = revs[org]
    churned = time.thread_time() - t0
    print(f"1% churn round ({len(changed):,} orgs, one row each): {churned * 1000:.0f} ms; "
          f"bodies changed={writes:,}")

    # idempotence: re-running an unchanged org writes nothing
    again = sum(
        1 for org in churn
        if body_hash(canonical_body(assemble(slugs[org], rows[org], memo)[0])) != written[org]
    )
    print(f"idempotence: re-projecting {len(churn):,} unchanged orgs -> {again} body changes")

    # memo memory at 20k orgs
    gc.collect()
    small = estate(20_000, per_org, rng)
    tracemalloc.start()
    base = tracemalloc.get_traced_memory()[0]
    m2: dict[int, tuple[bytes, tuple[str, object]]] = {}
    for org, org_rows in small.items():
        assemble(f"org-{org:06d}", org_rows, m2)
    used = tracemalloc.get_traced_memory()[0] - base
    tracemalloc.stop()
    print(f"row memo: {used / 1e6:.1f} MB for 100k rows -> {used / 100_000:,.0f} bytes/row; "
          f"linear extrapolation to 500k rows: {used * 5 / 1e6:,.0f} MB "
          f"(F1a-2 holds only the digest and the derived entry; F1a-3 adds the triple)")
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
