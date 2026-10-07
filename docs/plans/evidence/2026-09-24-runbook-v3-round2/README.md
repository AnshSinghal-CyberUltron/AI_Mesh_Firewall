# Runbook v3 — round-2 evidence index (2026-09-24)

Evidence behind Part 0 of `docs/AI_MESH_MASTER_RUNBOOK_v3_BACKEND_REWRITE.md` (the `.docx` sits next to it). Round 2 rebuilt the v2.1 target architecture as a measured prototype (rvproto-2, RC1 → RC2) and ran it on live GCP: project `ai-mesh-firewall`, asia-south1, plus asia-northeast1 for the 2-GPU unit and the split topology. Prices come from the Cloud Billing Catalog API (on-demand list prices). Secret rotation was out of scope by the owner's instruction.

This directory is an index: the key tables and the documents that explain them. The full evidence is kept on the controller VM (see below).

## Start here

| File | What it is |
|---|---|
| `docs/AI_MESH_MASTER_RUNBOOK_v3_BACKEND_REWRITE.md` (and `.docx`) | The v3 runbook. Part 0 has the verdict, measured facts, corrections register (R2-01 … R2-24), new cards, open gates (G-01 … G-18), cost basis, owner decisions and evidence state. v2.1's Part 0 is kept as Part 0-A. |
| `report/FINDINGS_LEDGER.md` | Every finding, with its numbers and the evidence path it came from. §S is round 2; the earlier sections are round 1. |
| `report/CONTROLLER_BULLETIN.md` | The rules and decisions the round-2 lanes ran under (B1 … B42), including the teardown (B42). |
| `v3/v3_notes.md`, `v3/rc3_tracker.md` | Working notes for each runbook section, and the state of RC3 (never cut). |

## Layout

- `report/`: the ledger, the bulletin and the round-2 spec.
- `v3/`:
  - `part0_v3.md`: the source of the new Part 0;
  - `assemble_v3.py`: v2.1 + Part 0 + amendment blocks → v3 `.md`;
  - `build_v3_index.py`: builds this directory;
  - the working notes.
- `prototype/`:
  - rvproto-2 source tarballs for RC1 and RC2, with sha256 files; the RC2 tarball is `eb9d4da2…`;
  - the manifests;
  - `IMAGES`, the image digests (the images themselves were deleted with the Artifact Registry repository);
  - the RC2 spec and usage notes.
- `patches/`: prototype patches with READMEs and sha256. They are reference implementations, not product code.
  - `d2-d1`: the redis-py 8 partition OOM and dead-socket 500.
  - `c36m-rc2`: RC3 input.
  - `rc3-state-p0`: freshness stamp, bounded Postgres sessions, two re-hydrators.
  - `rc3-audit-mem-v1`: the global audit budget.
  - `rc3-obs-v1`: typed exposition, reset-safe windows, GPU busy.
- `teardown/`:
  - the pre- and post-teardown inventories;
  - the teardown log, without its per-object bucket deletions;
  - the live re-check at 18:33Z;
  - the shared-Valkey audit trim record.
- `evidence/`: key result tables at their original paths, so the evidence paths in §0.2 resolve here too:
  - the edge bake-off;
  - fleet tables;
  - unit overload summaries;
  - C36 verdicts;
  - RC3 state functional results;
  - tenant-scale levels;
  - the five reviewers' pass-1 reports;
  - GPU alternatives;
  - the L4 pool log;
  - store drills;
  - MIG, GKE and chaos notes.

Text files larger than 256 KiB are stored as `.zst`. `MANIFEST.sha256` covers every file in this directory.

## Full evidence (not in git)

This evidence is kept only on the controller VM `ai-mesh-firewall`'s persistent disk. Nothing was copied off the VM, because the owner chose no further GCP spend (runbook §0.7-6).

- `/home/contact_cyberultron_com/rv-evidence-raw/`: per-lane raw runs, ≈ 154 GB.
- `/home/contact_cyberultron_com/rv-evidence-raw/round2-scratchpad-20260924/`: curated lane evidence and prototype trees, ≈ 8 GB. The `evidence/` paths in §0.2 are relative to this directory.
- `/home/contact_cyberultron_com/rv-evidence-gcs-round1-20260923/`: a verified copy of round 1's bucket `gs://ai-mesh-firewall-rv-evidence-20260923` (56,497 objects, 59,442,533,740 bytes). The bucket was deleted in the teardown, so the round-1 README's bucket paths now resolve to this directory.

## Rebuilding the runbook

```bash
python3 docs/plans/evidence/2026-09-24-runbook-v3-round2/v3/assemble_v3.py   # edit the paths at the top if needed
pandoc docs/AI_MESH_MASTER_RUNBOOK_v3_BACKEND_REWRITE.md -f gfm -t docx \
  --reference-doc docs/AI_MESH_MASTER_RUNBOOK_v2_BACKEND_REWRITE.docx \
  -o docs/AI_MESH_MASTER_RUNBOOK_v3_BACKEND_REWRITE.docx
```

## GCP state

The teardown ran from 17:32 to 18:08Z. It was re-checked live at 18:33Z (`teardown/live_inventory_20260924T1833Z.txt`), and every round-2 test resource is gone.

What remains:

- the controller VM, its disk and its static IP;
- another product's `aiguardx` snapshots;
- the default network;
- a zero-cost `servicenetworking` VPC peering. Google still holds it for the deleted Cloud SQL instances; the retry command is in the live-inventory file.

Round-2 spend is ≈ $435 at list prices. That figure is an estimate from 30-minute burn samples; the authoritative number is Cloud Billing for 2026-09-24.
