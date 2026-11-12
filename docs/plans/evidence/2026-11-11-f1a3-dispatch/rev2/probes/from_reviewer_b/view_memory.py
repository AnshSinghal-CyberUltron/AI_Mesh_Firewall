# Provenance: written by PR #121 Plan Reviewer B (review 5869723316) on the review host (probes/) and
# copied unchanged below this header for the F1a-3 revision 2 evidence, with credit.
"""PR #121 B probe: per-worker RSS of the plan's applied credential view at 100k orgs x 5 models.

Plan §2.6: the applied view holds, per entry, `{n, ct, aad}` "decoded from base64 once at apply
time", with the AAD bytes PRECOMPUTED per entry (to save 1.65 us per request), plus the wrapped
DEK per org. Every worker process holds its own copy (no sharing across uvicorn workers).
This builds exactly that shape (slotted SealedCredential with n, ct, aad; per-org SealedDek) for
ORGS x MODELS entries and reports the RSS delta, then the same without the precomputed AAD.
"""
import gc
import json
import os
import sys


class SealedCredential:
    __slots__ = ("n", "ct", "aad", "v")

    def __init__(self, n: bytes, ct: bytes, aad: bytes | None, v: int) -> None:
        self.n, self.ct, self.aad, self.v = n, ct, aad, v


class SealedDek:
    __slots__ = ("kid", "dek_id", "n", "w")

    def __init__(self, kid: str, dek_id: str, n: bytes, w: bytes) -> None:
        self.kid, self.dek_id, self.n, self.w = kid, dek_id, n, w


def rss() -> int:
    with open("/proc/self/statm") as f:
        return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")


def enc(*fields: str) -> bytes:
    out = bytearray()
    for f in fields:
        b = f.encode()
        out += len(b).to_bytes(4, "big") + b
    return bytes(out)


def build(orgs: int, models: int, precompute_aad: bool) -> int:
    gc.collect()
    r0 = rss()
    view = {}
    for o in range(orgs):
        org = f"org_{o:08d}"
        dek = SealedDek("k2026q4", f"d20261111-{o:08x}", os.urandom(12), os.urandom(48))
        entries = {}
        for m in range(models):
            alias = f"alias-{m}"
            aad = enc("amf.cred.v1", "prod", org, str(4000 + m), alias, "openai",
                      "https://api.openai.com/v1", "gpt-4o-2024-08-06", dek.dek_id, "3") \
                if precompute_aad else None
            entries[alias] = SealedCredential(os.urandom(12), os.urandom(164 + 16), aad, 3)
        view[org] = (dek, entries)
    gc.collect()
    delta = rss() - r0
    del view
    gc.collect()
    return delta


ORGS = int(sys.argv[1]) if len(sys.argv) > 1 else 100_000
MODELS = int(sys.argv[2]) if len(sys.argv) > 2 else 5
with_aad = build(ORGS, MODELS, True)
without_aad = build(ORGS, MODELS, False)
print(json.dumps({
    "python": sys.version.split()[0], "orgs": ORGS, "models_per_org": MODELS,
    "entries": ORGS * MODELS, "key_bytes": 164,
    "rss_mib_with_precomputed_aad": round(with_aad / 2**20, 1),
    "rss_mib_without_aad": round(without_aad / 2**20, 1),
    "bytes_per_entry_with_aad": round(with_aad / (ORGS * MODELS)),
    "note": "per worker process; excludes the routing table, aliases index, plans and the F1a-2 record cache",
}, indent=1))
