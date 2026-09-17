# T02 completeness vs docs/plans (2026-09-17)

Locked source of truth: `FINAL_AI_MESH_SCALABLE_GATEWAY_EXECUTION_RUNBOOK_WITH_FRONTEND_REVAMP.docx`.

That document's **T02** is the immutable Docker/staging lab (L02-1..L02-4), not the labeled corpus.

| Gate | Locked runbook | This lab | Notes |
|---|---|---|---|
| L02-1 recreate | required | PASS | Same image IDs; no repo bind mounts. `l02_1_recreate.json` |
| L02-2 ALLOW/REDACT/BLOCK | recorder original / sanitized / zero calls | PASS | `zs-e6e611e587a9` / `zs-1d720143ca49` / `zs-e37efd2772ce`. BLOCK used org keyword canary; `pipeline_trace.model_output` still reads allow while recorder_calls=0 |
| L02-3 second host | required | WAIVED | Same waiver as L00-2 |
| L02-4 fault-control not public | required | PASS | Admin `127.0.0.1:18081`; `:8080` unpublished |
| 20 ms / 1064 RPS | later tasks (T03/T18/T23) | not claimed | Open-loop ~18 rps / p99 ~1841 ms is lab-only |
| Request-ID join bundle | exit evidence | PARTIAL | API request IDs exist; full browser→audit bundle not packaged as one file |

## Other docs (do not mix numbering)

- `IMPLEMENTATION_RUNBOOK.md` **T02** is G0 labeled corpus (300+300). That is **T04** in the locked docx. Corpus is **not** done.
- `IMPLEMENTATION_RUNBOOK_V3.md` uses A00–A19. H16 wants immutable release images. Vite on `:8180` is a **lab UI switch after T02 snapshot**, not a release claim.
- `START_HERE.md` still describes T02 as corpus and T03 as timing. Follow the locked docx for this program.

## Verdict

- **Lab T02 (locked runbook L02):** PASS with signed L02-3 waiver. Not runbook-verbatim.
- **Corpus T02/T04:** NOT complete.
- **T03:** not started (user lock: Vite only this session). T03 in the locked docx depends on T00,T01,T02 and owns timing/`T_fw_addon` truth.
