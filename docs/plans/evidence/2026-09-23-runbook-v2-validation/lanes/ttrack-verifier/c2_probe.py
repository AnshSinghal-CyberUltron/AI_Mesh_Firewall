"""Probe the GW02 C2 corpus + replay at HEAD. Read-only; imports repo code."""
import collections, json, sys
from gateway_v2.contracts.parity.c2 import generate_c2, coverage_report
from gateway_v2.contracts.parity.clock import FrozenClock
from gateway_v2.contracts.parity.replay import replay
from gateway_v2.contracts.parity.v1_oracle import v1_disposition
recs = generate_c2(50_000, FrozenClock(epoch=1_704_067_200))
uniq = collections.Counter(r.prompt for r in recs)
per_cell = collections.defaultdict(set)
for r in recs:
    per_cell[f"{r.surface}|{r.mode}|{r.tenant}"].add(r.prompt)
# does replay outcome depend on tenant/plan? compare same prompt across tenants
by_prompt_tenant = collections.defaultdict(set)
for r in recs[:2000]:
    o = replay(r, FrozenClock(epoch=1_704_067_200), v1_disposition)
    by_prompt_tenant[r.prompt].add((r.tenant, o.disposition))
out = {
  "n": len(recs),
  "unique_prompts": len(uniq),
  "prompt_counts": dict(uniq),
  "unique_prompts_per_cell": {k: len(v) for k, v in sorted(per_cell.items())},
  "disposition_by_prompt_and_tenant(first 2000)": {p: sorted(map(list, s)) for p, s in by_prompt_tenant.items()},
  "c2_record_fields": list(recs[0].__dataclass_fields__),
}
json.dump(out, sys.stdout, indent=2)
