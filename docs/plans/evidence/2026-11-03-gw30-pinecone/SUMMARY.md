# GW30 — Pinecone live-acceptance gate: evidence SUMMARY

Run date: 3 November 2026. Branch `gw30-pinecone`,
worktree `/work/suraj/AI_Mesh_Firewall/.worktrees/gw30-pinecone`.
Real Pinecone serverless API (SDK `pinecone==10.3.0`). A GW card, NOT an R2 item.

Credential reference: `cred-pinecone-gw30`. The API key lives ONLY in the gitignored
`/work/suraj/AI_Mesh_Firewall/.env.gw30-pinecone`; it is NEVER printed, logged, or written into
any evidence file (verified by a key scan across this directory — no match).

Discovered account placement (from `list_indexes()` before the run): cloud `aws`, region
`us-east-1` (the account had zero existing indexes; defaults adopted). Gate index:
`gw30-live-gate` (dimension 4, metric cosine), a throwaway, deleted at the end of the run.

## Gate verdicts

| Gate | Verdict | Evidence file | One-line result |
|---|---|---|---|
| L30-1 | **LIVE-PASS** | `L30-1_pinecone_parity.json` | create→`vector_ok`, upsert 3, query ids `r1,r3,r2`, delete 3 — all `vector_ok` |
| L30-1b | **LIVE-PASS** | `L30-1b_create_preserves_data.json` | first create `vector_ok`, second create `collection_exists`, 3 records intact |
| L30-2 | **LIVE-PASS** | `L30-2_pinecone_unreachable.json` | bogus index → `provider_unreachable`, `records is None`, 0.28 s (< 30 s), not a clean empty search |
| L30-3 | **LIVE-PASS** | `L30-3_pinecone_refusal.json` | `credential_scope_mismatch` + `org_identity_invalid`, 0 backing-store ops, no secret in refusal form |

## Live ordering fact

The L30-1 query returned ids `["r1","r3","r2"]` — this is Pinecone's OWN cosine search result
(highest similarity first), not a client-side sort of ids. With `QVEC = (1,0,0,0)`: `r1` is
identical to the query (score 1.0), `r3 = (0.9,0.1,0,0)` is close (≈0.994), and `r2 = (0,1,0,0)`
is orthogonal (0.0); `records_from_scored` orders by Pinecone's returned scores. L30-1b after the
second create returned the same `r1,r3,r2`, and `sorted(ids) == ["r1","r2","r3"]` confirms all
three records survived the non-destructive re-create.

## Eventual consistency

Pinecone serverless upserts apply asynchronously, so the harness POLLS `query` for the namespace to
report `>= 3` queryable vectors (bounded ~30 s, 1.5 s between polls) BEFORE asserting the ordering.
In this run the vectors were queryable on the first poll attempt.

## Cleanup

`cleanup: {"ran": true, "index_deleted": "gw30-live-gate", "already_absent": false}` — the throwaway
gate index was deleted at the end of the run; a post-run `list_indexes()` shows it absent.

## Secret hygiene

NO key material appears in any evidence file (per-gate JSON, `live_run.txt`, `SUMMARY.md`, or the
harness). The credential is referenced only by its ref id `cred-pinecone-gw30`. The transport carries
the key in a dataclass field but adds no `__repr__` that prints it; the harness loads it from the
gitignored `.env.gw30-pinecone` and never echoes it.

## Files

- `run_live_gates_pinecone.py` — the harness (imports the shipped 9store machinery; overrides only
  `LIVE_TRANSPORTS` / `INFRA_BLOCKED` + Pinecone-specific L30-1/L30-1b/L30-2/L30-3).
- `L30-1_pinecone_parity.json`, `L30-1b_create_preserves_data.json`,
  `L30-2_pinecone_unreachable.json`, `L30-3_pinecone_refusal.json` — per-gate JSON.
- `live_run.txt` — captured stdout.
