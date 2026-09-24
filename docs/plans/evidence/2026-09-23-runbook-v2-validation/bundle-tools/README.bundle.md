# Runbook v2 validation — evidence bundle (2026-09-23)

Evidence behind the verdict on `docs/AI_MESH_MASTER_RUNBOOK_v2_BACKEND_REWRITE.docx` and the corrected runbook v2.1.
Validated against `revamp @ 52a584e9` and the runbook's audited baseline `ansh @ 2a657fad`, on live GCP (project
`ai-mesh-firewall`; asia-south1, plus asia-northeast1 after G2 stock-outs), priced at on-demand list prices from the
Cloud Billing Catalog API. Security hygiene (secret rotation) was out of scope by the owner's instruction.

## Start here

| File | What it is |
|---|---|
| `runbook/out/AI_MESH_MASTER_RUNBOOK_v2.1_BACKEND_REWRITE.docx` (and `.md`) | The corrected runbook. Part 0 = verdict, measured facts, corrections register, new cards, corrected task index; every changed section carries a "v2.1 CORRECTION" block. |
| `report/page/runbook-v2-verdict.html` | The verdict page (also published as a private claude.ai artifact). |
| `report/FINDINGS_LEDGER.md` | Every finding with its numbers and the evidence path it came from. |

## Layout

- `runbook/` — v2.1 sources (`part0_*.md`, `blocks.md`) and `build_v21.py`; `runbook/v2-source/` is the v2 docx as markdown.
- `lanes/<lane>/` — each validation lane's scripts, per-run summaries and tables (claim verifiers, GPU guard bench, v1 bench,
  micro-claims, prototype build, unit/fleet/split benches, five contradiction reviewers).
- `harness/` — the load harness source (olg load generator, synthprov provider/recorder, rvproxy, analyze.py); binaries are
  identified by sha256 in `harness/READY`.
- `rvproto/` — the throwaway v2 prototype (`rvproto-frozen-1`); the loop-isolation diff is
  `lanes/proto-bench-unit/loop-isolation/li-knobs.diff`.
- `raw/` — a small sample of per-request raw data (olg + synthprov + gateway dumps) for runs behind published numbers.
- `specs/`, `bundle-tools/` — lane specs, this bundle's builder, the spend calculator.

## Raw data

The complete per-request raw data (≈ 50 GB, already zstd-compressed) is in the private bucket
`gs://ai-mesh-firewall-rv-evidence-20260923` (project `ai-mesh-firewall`, public access prevention enforced).
`EXCLUDED.tsv.zst` (zstd-compressed TSV) lists every file that is not in git with its size, sha256 and bucket location. `MANIFEST.sha256` covers
every file in this directory. Text files larger than 256 KiB are stored as `.zst`.

## Reproducing a number

- C4 (worst chunk per stream, holdback excluded) for a run: `python3 lanes/proto-bench-fleet/scripts/c4_client_all.py <raw run dir> --all`
  (needs `orjson`; the raw run directories are in the bucket under `rv-evidence-raw/`).
- Harness strata and T_fw_addon: `python3 harness/analyze.py <raw run dir>`.
- Independent recomputation of every published unit, fleet and v1 number: `lanes/reviewer-observability/` (`recompute.py`, `run_unit.sh`).
- Spend from Cloud Audit Logs: `python3 bundle-tools/spend_audit.py`.
