"""P3a/b on the LIVE 4-worker rvproto: revocation bound across workers.
 a) HDEL + INCR rv:auth_epoch (MULTI): time until every fresh-connection probe is 401
 b) HDEL only (a revocation path that forgets the epoch, e.g. v1 console semantics): cached workers
"""

from __future__ import annotations

import hashlib
import json
import time

from pc import log, outcome, probe, r

ORG = "org-b"


def add_key(k: str) -> str:
    h = hashlib.sha256(k.encode()).hexdigest()
    r().hset("rv:keys", h, json.dumps({"key_id": k[-6:], "org_id": ORG, "rate_per_s": 1e6, "burst": 1e6, "epoch": 1}))
    return h


def warm(k: str) -> dict:
    out: dict = {}
    for _ in range(24):
        o = outcome(probe(k))
        out[o] = out.get(o, 0) + 1
    return out


def until_all_401(k: str, limit: float) -> tuple[float | None, list]:
    t0 = time.time()
    seen = []
    streak = 0
    while time.time() - t0 < limit:
        o = outcome(probe(k))
        seen.append((round(time.time() - t0, 3), o[:3]))
        streak = streak + 1 if o.startswith("401") else 0
        if streak >= 16:
            first_of_streak = seen[-16][0]
            return first_of_streak, seen
    return None, seen


def main() -> None:
    k1 = "sk-rv-revocation-probe-a1"
    h1 = add_key(k1)
    log("a0 new key warmed on the workers", warm=warm(k1))
    p = r().pipeline(transaction=True)
    p.hdel("rv:keys", h1)
    p.incr("rv:auth_epoch")
    p.execute()
    t_all, seen = until_all_401(k1, 10)
    last_200 = max((x[0] for x in seen if x[1] == "200"), default=None)
    log("a1 revoked with epoch bump", every_probe_401_from_s=t_all, last_accept_after_revocation_s=last_200,
        n_probes=len(seen))
    k2 = "sk-rv-revocation-probe-b1"
    h2 = add_key(k2)
    time.sleep(1.0)
    log("b0 second key warmed", warm=warm(k2))
    r().hdel("rv:keys", h2)  # no epoch bump
    t_all, seen = until_all_401(k2, 20)
    acc = sum(1 for x in seen if x[1] == "200")
    log("b1 revoked WITHOUT epoch bump (store has no key)", every_probe_401_within_20s=t_all,
        accepted_after_revocation=acc, of=len(seen), last=seen[-3:])


if __name__ == "__main__":
    main()
