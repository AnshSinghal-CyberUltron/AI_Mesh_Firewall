"""F1a-2 evidence: MODEL record size, sign/verify cost, per-org applier cost, view memory, lookup.

Run from `gateway_v2/` with the gateway venv (it imports HEAD's real signing code):

    nice -n 10 ../../venv-ci/bin/python -I ../docs/plans/evidence/2026-11-11-f1a2-model-state/model_record_bench.py

Every timing is THREAD CPU (`time.thread_time`), because the 2-CPU host is shared with other
agents and wall time measures them as much as this code. The parser and the view are a
PROTOTYPE of the design in the plan (`OrgModels`, `ModelEntry`), not product code; the record
envelope, canonical body, HMAC and decode are HEAD's real `runtime/state_sig.py`.
"""

from __future__ import annotations

import base64
import gc
import json
import os
import random
import re
import sys
import time
import tracemalloc
from collections import OrderedDict
from dataclasses import dataclass
from types import MappingProxyType

sys.path.insert(0, ".")

from gateway_v2.domain.state import StateKind, Version  # noqa: E402
from gateway_v2.runtime.state_sig import (  # noqa: E402
    canonical_body,
    decode_record,
    encode_record,
    make_record,
)

SECRET = b"f1a2-evidence-not-a-real-key"
MODEL = StateKind.KEY  # HEAD has no MODEL kind; the envelope cost does not depend on the name

PROVIDERS = frozenset({
    "openai", "anthropic", "azure", "google", "aws_bedrock", "mistral", "cohere", "deepseek",
    "huggingface", "meta", "ollama", "custom",
})
ALIAS = re.compile(r"[A-Za-z0-9._:/-]{1,128}")
ALIAS_KEY = re.compile(r"[a-z0-9._:/-]{1,128}")
UPSTREAM = re.compile(r"[A-Za-z0-9._:/@+-]{1,256}")
KID = re.compile(r"[a-z0-9_-]{1,32}")
B64 = re.compile(r"[A-Za-z0-9+/=]{1,8192}")
SENSITIVITY = ("public", "internal", "confidential", "restricted")
TAGS = frozenset({"SOC2", "HIPAA", "GDPR", "PCI-DSS", "ISO27001", "DPDPA", "CCPA", "FEDRAMP"})
OMIT_REASONS = frozenset({
    "inactive", "credential_missing", "credential_env_var", "credential_undecryptable",
    "alias_invalid", "alias_collision", "upstream_invalid", "unknown_provider", "base_url_invalid",
    "region_invalid", "row_invalid", "org_model_cap",
})
MAX_MODELS = 64
MAX_OMITTED = 64
MAX_RECORD_BYTES = 512 * 1024


# --- body builders ------------------------------------------------------------------------------


def _cred(rng: random.Random, key_bytes: int) -> dict[str, object]:
    return {
        "t": "aead1",
        "kid": "k2026q4",
        "nonce": base64.b64encode(os.urandom(12)).decode(),
        "ct": base64.b64encode(os.urandom(key_bytes + 16)).decode(),
    }


def entry(rng: random.Random, i: int, *, worst: bool) -> dict[str, object]:
    if worst:
        alias = ("a" * 120 + f"{i:08d}")[:128]
        return {
            "key": alias, "alias": alias, "row": 10**12 + i, "provider": "custom",
            "upstream": ("u" * 248 + f"{i:08d}")[:256],
            "base_url": "https://" + "h" * 400 + f".example.com/{i:08d}/v1",
            "region": "r" * 64, "created": 1_731_300_000 + i,
            "tags": sorted(TAGS), "sensitivity": "restricted",
            "cred": _cred(rng, 4096),
        }
    provider = rng.choice(("openai", "anthropic", "azure", "aws_bedrock", "google"))
    alias = rng.choice(("gpt-4o", "gpt-4o-mini", "claude-sonnet", "prod-chat", "gemini-pro")) + f"-{i}"
    out: dict[str, object] = {
        "key": alias.lower(), "alias": alias, "row": rng.randint(1, 10**7), "provider": provider,
        "upstream": rng.choice(("gpt-4o-2024-08-06", "claude-3-5-sonnet-20241022",
                                "anthropic.claude-3-5-sonnet-20240620-v1:0", "gemini-1.5-pro")),
        "base_url": "https://acme-prod.openai.azure.com" if provider == "azure" else None,
        "region": "us-east-1" if provider == "aws_bedrock" else None,
        "created": 1_731_300_000 + rng.randint(0, 10**6),
        "tags": sorted(rng.sample(sorted(TAGS), 2)), "sensitivity": rng.choice(SENSITIVITY),
        "cred": _cred(rng, 51),
    }
    return out


def body(org: str, rng: random.Random, n: int, *, worst: bool = False, omitted: int = 0) -> dict[str, object]:
    models = sorted((entry(rng, i, worst=worst) for i in range(n)), key=lambda e: str(e["key"]))
    om = sorted(
        ({"key": f"{'x' * 100}{i:08d}", "alias": f"{'X' * 100}{i:08d}", "reason": "credential_env_var"}
         for i in range(omitted)),
        key=lambda e: e["key"],
    )
    return {
        "schema": 1, "org_id": org, "models": models, "omitted": om, "omitted_overflow": 0,
        "allowed": None, "default": models[0]["key"] if models else None,
    }


# --- gateway-side prototype parser and view ------------------------------------------------------


class BodyInvalid(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Cred:
    tag: str
    kid: str | None
    nonce: str | None
    ct: str | None


@dataclass(frozen=True, slots=True)
class ModelEntry:
    key: str
    alias: str
    row: int
    provider: str
    upstream: str
    base_url: str | None
    region: str | None
    created: int
    tags: frozenset[str]
    sensitivity: int
    cred: Cred


@dataclass(frozen=True, slots=True)
class OrgModels:
    org: str
    version: Version
    feed_seq: int
    by_key: MappingProxyType[str, ModelEntry]
    omitted: MappingProxyType[str, str]
    allowed: frozenset[str] | None
    default: str | None
    listing: tuple[tuple[str, int, str], ...]
    dropped: int


def _s(v: object, rx: re.Pattern[str]) -> str:
    if not isinstance(v, str) or rx.fullmatch(v) is None:
        raise ValueError
    return v


def _entry(raw: object) -> ModelEntry:
    if not isinstance(raw, dict):
        raise BodyInvalid("entry not an object")
    keys = set(raw)
    if keys != {"key", "alias", "row", "provider", "upstream", "base_url", "region", "created",
                "tags", "sensitivity", "cred"}:
        raise ValueError("unknown or missing field")  # -> this entry is omitted, counted
    provider = raw["provider"]
    if provider not in PROVIDERS:
        raise ValueError("unknown provider")
    alias = _s(raw["alias"], ALIAS)
    key = _s(raw["key"], ALIAS_KEY)
    if alias.casefold() != key:
        raise BodyInvalid("key is not the casefolded alias")
    row = raw["row"]
    created = raw["created"]
    if isinstance(row, bool) or not isinstance(row, int) or row < 1:
        raise BodyInvalid("row")
    if isinstance(created, bool) or not isinstance(created, int) or created < 0:
        raise BodyInvalid("created")
    base_url = raw["base_url"]
    if base_url is not None and (not isinstance(base_url, str) or not 8 <= len(base_url) <= 512):
        raise BodyInvalid("base_url")
    region = raw["region"]
    if region is not None and (not isinstance(region, str) or not 1 <= len(region) <= 64):
        raise BodyInvalid("region")
    tags = raw["tags"]
    if not isinstance(tags, list) or not all(t in TAGS for t in tags):
        raise ValueError("unknown tag")
    sens = raw["sensitivity"]
    if sens not in SENSITIVITY:
        raise ValueError("unknown sensitivity")
    c = raw["cred"]
    if not isinstance(c, dict) or c.get("t") not in ("aead1", "pending", "none"):
        raise ValueError("unknown cred tag")
    if c["t"] == "aead1":
        if set(c) != {"t", "kid", "nonce", "ct"}:
            raise ValueError("cred fields")
        cred = Cred("aead1", _s(c["kid"], KID), _s(c["nonce"], B64), _s(c["ct"], B64))
    else:
        if set(c) != {"t"}:
            raise ValueError("cred fields")
        cred = Cred(c["t"], None, None, None)
    return ModelEntry(key, alias, row, provider, _s(raw["upstream"], UPSTREAM), base_url, region,
                      created, frozenset(tags), SENSITIVITY.index(sens), cred)


def parse(org: str, version: Version, feed_seq: int, raw_body: bytes) -> OrgModels:
    """Whole-body defects raise BodyInvalid (-> org-level unavailable(version)); an entry with an
    unknown provider or field is dropped and counted. Never raises anything else by design;
    the applier still wraps it in `except Exception`."""
    if len(raw_body) > MAX_RECORD_BYTES:
        raise BodyInvalid("record over the size bound")
    doc = json.loads(raw_body)
    if not isinstance(doc, dict) or doc.get("schema") != 1 or doc.get("org_id") != org:
        raise BodyInvalid("schema or org")
    models = doc.get("models")
    omitted = doc.get("omitted")
    if not isinstance(models, list) or len(models) > MAX_MODELS:
        raise BodyInvalid("models")
    if not isinstance(omitted, list) or len(omitted) > MAX_OMITTED:
        raise BodyInvalid("omitted")
    by_key: dict[str, ModelEntry] = {}
    dropped = 0
    for raw in models:
        try:
            e = _entry(raw)
        except ValueError:
            dropped += 1
            continue
        if e.key in by_key:
            raise BodyInvalid("duplicate key")
        by_key[e.key] = e
    om: dict[str, str] = {}
    for raw in omitted:
        if not isinstance(raw, dict) or raw.get("reason") not in OMIT_REASONS:
            raise BodyInvalid("omitted entry")
        om[_s(raw.get("key"), ALIAS_KEY)] = raw["reason"]
    allowed_raw = doc.get("allowed")
    allowed = None if allowed_raw is None else frozenset(_s(a, ALIAS_KEY) for a in allowed_raw)
    default = doc.get("default")
    listing = tuple(
        (e.alias, e.created, e.provider) for k, e in sorted(by_key.items())
        if allowed is None or k in allowed
    )
    return OrgModels(org, version, feed_seq, MappingProxyType(by_key), MappingProxyType(om),
                     allowed, default if isinstance(default, str) else None, listing, dropped)


# --- measurements ------------------------------------------------------------------------------


def per_op(fn, n: int) -> float:
    gc.collect()
    t0 = time.thread_time()
    for _ in range(n):
        fn()
    return (time.thread_time() - t0) / n * 1e6


def main() -> int:
    rng = random.Random(20261111)
    print(f"python={sys.version.split()[0]} (thread CPU; host shared with other agents)")
    shapes = {
        "empty org (0 models)": body("org-00001", rng, 0),
        "typical (5 models, 51-byte keys)": body("org-00001", rng, 5),
        "large (20 models)": body("org-00001", rng, 20),
        "64 typical models (the per-org cap)": body("org-00001", rng, 64),
        "64 typical + 64 omitted": body("org-00001", rng, 64, omitted=64),
        "worst (64 max-length models, 4 KiB keys, 64 omitted)": body("org-00001", rng, 64, worst=True, omitted=64),
    }
    print("\n## Record sizes and costs (per record)")
    print("| shape | canonical body bytes | envelope bytes | make_record us | decode_record us | parse+build us | apply total us |")
    print("|---|---|---|---|---|---|---|")
    for label, b in shapes.items():
        canon = canonical_body(b)
        rec = make_record(SECRET, MODEL, "org-00001", b, Version(1, 7), 7)
        env = encode_record(rec)
        n = 2000 if "worst" not in label else 200
        mk = per_op(lambda: make_record(SECRET, MODEL, "org-00001", b, Version(1, 7), 7), n)
        dec = per_op(lambda: decode_record(SECRET, MODEL, env), n)
        prs = per_op(lambda: parse("org-00001", Version(1, 7), 7, rec.body), n)
        print(f"| {label} | {len(canon):,} | {len(env):,} | {mk:.1f} | {dec:.1f} | {prs:.1f} | {dec + prs:.1f} |")
        assert len(canon) <= MAX_RECORD_BYTES, label

    # idempotence: a permuted source produces identical bytes
    typical = shapes["typical (5 models, 51-byte keys)"]
    ref = canonical_body(typical)
    for _ in range(1000):
        models = list(typical["models"])  # type: ignore[arg-type]
        rng.shuffle(models)
        again = dict(typical, models=sorted(models, key=lambda e: e["key"]))
        assert canonical_body(again) == ref
    print("\nidempotence: 1000/1000 shuffled sources -> byte-identical canonical body")

    # memory per OrgModels (typical) in a populated view
    print("\n## View memory (tracemalloc, typical 5-model org, ciphertext only)")
    recs = []
    for i in range(10_000):
        b = body(f"org-{i:06d}", rng, 5)
        recs.append((f"org-{i:06d}", canonical_body(b)))
    gc.collect()
    tracemalloc.start()
    base = tracemalloc.get_traced_memory()[0]
    view: OrderedDict[str, OrgModels] = OrderedDict()
    for org, raw in recs:
        view[org] = parse(org, Version(1, 1), 1, raw)
    used = tracemalloc.get_traced_memory()[0] - base
    tracemalloc.stop()
    print(f"10,000 orgs x 5 models: {used / 1e6:.1f} MB -> {used / 10_000:,.0f} bytes per org")
    per_org = used / 10_000
    for share_mb in (20, 40):
        print(f"  a {share_mb} MiB view share holds {int(share_mb * 1024 * 1024 // per_org):,} typical orgs")
    empty_used = None
    tracemalloc.start()
    base = tracemalloc.get_traced_memory()[0]
    empties = [parse(f"org-{i:06d}", Version(1, 1), 1, canonical_body(body(f"org-{i:06d}", rng, 0))) for i in range(10_000)]
    empty_used = tracemalloc.get_traced_memory()[0] - base
    tracemalloc.stop()
    del empties
    print(f"10,000 empty orgs: {empty_used / 10_000:,.0f} bytes per org")

    # rev 2 (Reviewer B5): bytes per org by shape, and the byte charge the view uses
    print("\n## View bytes by org shape vs the rev-2 charge (3 x canonical bytes + 512)")
    print("| shape | canonical bytes | measured view bytes/org | charge | measured / charge |")
    print("|---|---|---|---|---|")
    for label, n, kwargs in (
        ("typical 5 models", 5_000, {"n": 5}),
        ("64 typical models", 400, {"n": 64}),
        ("64 typical + 64 omitted", 400, {"n": 64, "omitted": 64}),
        ("worst (64 max, 4 KiB keys, 64 omitted)", 40, {"n": 64, "worst": True, "omitted": 64}),
    ):
        raws = [(f"org-{i:06d}", canonical_body(body(f"org-{i:06d}", rng, **kwargs))) for i in range(n)]
        gc.collect()
        tracemalloc.start()
        base = tracemalloc.get_traced_memory()[0]
        held = [parse(org, Version(1, 1), 1, raw) for org, raw in raws]
        used = (tracemalloc.get_traced_memory()[0] - base) / n
        tracemalloc.stop()
        del held
        canon = sum(len(r) for _, r in raws) / n
        charge = 3 * canon + 512
        print(f"| {label} | {canon:,.0f} | {used:,.0f} | {charge:,.0f} | {used / charge:.2f} |")
        assert used <= charge, label

    # request path: lookup cost at 1k vs 100k orgs
    print("\n## Request-path lookup (pin org + resolve alias), ns per lookup")
    proto = parse("org-00001", Version(1, 1), 1, canonical_body(typical))
    alias_keys = list(proto.by_key)
    for size in (1_000, 100_000):
        v: OrderedDict[str, OrgModels] = OrderedDict((f"org-{i:06d}", proto) for i in range(size))
        orgs = [f"org-{rng.randrange(size):06d}" for _ in range(200_000)]
        als = [rng.choice(alias_keys) for _ in range(200_000)]

        def run() -> None:
            get = v.get
            mte = v.move_to_end
            for o, a in zip(orgs, als):
                om = get(o)
                mte(o)
                om.by_key.get(a)  # type: ignore[union-attr]

        best = min(per_op(run, 1) for _ in range(5))
        print(f"  {size:>7,} orgs: {best * 1000 / len(orgs):.0f} ns per lookup (best of 5, 200k lookups)")
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
