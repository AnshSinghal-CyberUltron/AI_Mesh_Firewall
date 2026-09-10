# P0.0 requirements review — accumulating notes

Patched into `requirements.md` only after **all five** lenses return (avoids double-edits).

## Observability — [P0.0 req observability](7d50b700-abf8-4e72-a5ff-43ee6f49a5af)

**Verdict:** Reqs 7, 9, 15 already forbid streaming / UI Duration as the 20 ms tax. G1.1 is not on `a67337fb`. Remaining holes are documentation of *why*, plus scorecard row (b) and Req 11 silence.

**Accept when patching:**

1. **Req 9:** Pack must state that on this SHA, stream `total − model_output` **equals `ttft_ms`**, and stream `total_latency_ms` is `now − provider_start_ts` (pre-model tax excluded).
2. **Req 7.5 / 15.3:** Also forbid `processing_time_ms`, stream `t_addon_pre/post`, Prometheus `stream_ttft_seconds` / `stream_duration_seconds` / chat duration histograms as tax.
3. **Req 5:** `addon_definition` is **non-stream only**; streaming cells must not inherit that string.
4. **Req 11:** CPU% and RPS SHALL NOT be compared to 20 ms; cgroup CPU% is not MASTER “CPU-only tax.”
5. **Req 13.1(b):** Report Wall; do **not** PASS/FAIL it against 20 ms (`N/A-not-tax`).

**Keep:** 7.1 Cell_A-only comparator; 9.2 N/A-instrument; 9.3 no G1.1 implementation; 15.2 `full_nine_stages` is two timers.

## Edge cases — [P0.0 req edge cases](82b2784a-edaa-49f4-a306-149409c1b2a3)

**Verdict:** Extra labelled cells do not keep Cell_A honest. The reused bench percentiles mix every `addon_ms` (including 400 content-filter blocks). Input terminal block is HTTP **400**, not 403. Redact is HTTP **200**.

**Accept when patching:**

1. **Cell_A filter:** only HTTP 200, `stream=false`, `final_action=allow` (no redact), model stages not `skip`. Drop 400 `content_filter`, 403, 401, 429, 503, status 0.
2. **Req 6.4 / 8.1 / 8.4:** replace “403 terminal-block” with content-filter **400** + 403 + skipped-model / `is_terminal_block`.
3. **Req 8:** extra cells stay labelled and never concatenated; they do not replace the allow-path filter.
4. **Explicit N/A cells:** kill-switch, output-guard block, stream abort, unpinned-worker *cell* (document reasons). Cell_A still documents kill-switch not armed; exclude 503.
5. **Pinned `WEB_CONCURRENCY` + `cpus` required on Cell_A itself**; optional burst stays out of tax p50.

**Keep:** 10k-char as a separate row; scans-off OR empty-policy as a negative label (Preflight is the real scans-on gate).

## False positives — [P0.0 req false positives](c58eeca5-10d9-4e44-95c1-7805dc1dc4b9)

**Verdict:** Isolation, overlay pins, preflight, Cell_A-only 20 ms, reuse of the existing bench, and labelled block/redact/scans-off rows stay. Several ACs restate shipped honesty code or later gates.

**Accept when patching (shrink/drop):**

1. **Req 5 AC2:** shrink — re-running unit honesty tests is not the live stack (optional one-liner in evidence, not a gate).
2. **Req 5 AC5:** drop — implied by reusing `build_honesty_report`.
3. **Req 7 AC3:** do **not** zero-fill missing stages (shipped bench omits them).
4. **Req 8 AC4** forced 401+429: drop as a tax cell (exclusion stays in Cell_A filter).
5. **Req 8 AC5** ~10k-char: **conflict** with edge cases (see below). FP says drop (`MAX_PROMPT_LENGTH=10000` + nonce → dos 400).
6. **Req 9 whole streaming cell:** shrink to a one-line N/A-instrument + TTFT-alias note (observability still wants the *why*; no streaming *run* required).
7. **Req 4 AC7** per-worker `/health`: drop — health has no worker id; pin `WEB_CONCURRENCY` instead.
8. **Req 4 AC4:** record digest **and** SHA; fail on compose **label** `git.sha == HEAD`, not digest-string-equals-SHA.
9. **Req 4 AC3:** T2 from overlay env / `docker exec`, not `/health`.
10. **Req 10 T2-on:** MAY, not SHALL.
11. **Req 13 (e)(f)(g):** fold (e) into Cell_A; (f)(g) stay one-line N/A, not gates.
12. **Req 14:** fold one disclaimer into Req 12; drop as a standalone requirement.
13. **Req 11 burst:** shrink to Cell_A mid-window CPU only.

**Keep:** Req 1–3 pins, 4.1/2/5/6/8, 5.1/3/4, 6 unique-prompt + N≥16, 7.1/2/5/6/7, 8.1–8.3, 12, 13 a–d/h, 15.1/4/5.

## Stale state — [P0.0 req stale state](069a0ddb-467c-443e-9056-4db02d3d1f11)

**Verdict:** Req 2–4 are necessary but not a closed gate. Wrong image, last-good policy, reused Redis AOF, and live `:8300` can still print a 20 ms-looking tax.

**Accept when patching:**

1. **SHA identity:** overlay `build.labels.git.sha`; preflight `docker inspect` equals `HEAD`; `--build`; `GATEWAY_URL` peer has compose project label `aimf_p0`.
2. **Unpublish/remap** 8300/8100/6432 so curl cannot hit Dirty_Ansh / aimeshperf.
3. **Signing:** literal identical `POLICY_SIGNING_KEY` on control+gateway (no `${…:-}`); in-container `printenv` fingerprint; HMAC last-good ≠ health-200-with-empty-cache.
4. **Volumes:** harness-enforced `down -v` or unique prefix; Redis empty **before** seed; bundle identity (`compiled_at` / policy ids), not just `policy_count` (count is cross-org).
5. **ConfigSync:** org Redis or a chat probe for `input_scan_enabled` / `tier2_enabled` / `enforcement_mode`; pin `GATEWAY_INPUT_SCAN_ENABLED`.
6. **Workers:** `WEB_CONCURRENCY=1` **or** a non-`/health` per-PID check; in-container T2 TTL=0 when T2 is on.

## Conflicts to resolve in Devil's Advocate (after the last lens)

| Topic | False positives | Edge cases / stale state / observability |
|---|---|---|
| 10k-char cell | Drop | Edge: keep as separate row |
| Streaming Req 9 | Drop the cell | Obs: keep TTFT-alias *text*; no run required → **text-only wins** |
| Forced 401/429 | Drop | Edge: exclusion via Cell_A filter is enough → **drop smokes** |
| Per-worker health | Drop | Stale: `WEB_CONCURRENCY=1` instead → **pin 1 on Cell_A** |
| Digest vs SHA | Record both, no equality | Stale: compose **label** equality → **label gate** |

## Hidden failures — [P0.0 req hidden failures](e6aa77a8-e7e4-4db6-9679-796973df59bd)

**Verdict:** Spec still lets a cheaper/wrong pipeline print ≤20 ms. Largest holes: host `:8300` vs `aimf_p0`, `/health` global CONFIG vs per-org Redis, two timers as completeness, unique-prompt default off, stub duration OR, interpolated overlay pins.

**Accept when patching:** HTTP target is `aimf_p0`; org Redis config; org-scoped policies; unique bodies; duration AND token floor; literal pins; compose `git.sha` label; nine `action != skip` on counted samples.

## Devil's Advocate lock (2026-09-10) — all five lenses in

| Conflict | Decision |
|---|---|
| 10k-char | Size cell at **4096** unique chars (under cap). Never vs 20 ms. |
| Streaming cell | **No run.** Pack text: stream `total−model_output == ttft_ms`. |
| 401/429 smokes | **Drop.** Cell_A filter excludes them. |
| Per-worker `/health` | **Drop.** Cell_A `WEB_CONCURRENCY=1`. |
| Digest == SHA | Compose **label** `git.sha` equals `HEAD`. |
| `full_nine_stages` | Not sufficient. Counted samples: all nine `action != skip`. No zero-fill. |
| T2-on | **MAY**; else `N/A-measured`. |
| Req 14 | Folded into evidence pack. |
| Grounding default true | **Pin false** on Cell_A; label the posture. |
| Scorecard wall vs 20 ms | Wall is **N/A-not-tax**. |

`requirements.md` patched. Next: `design.md`.

## Design-DA tightenings (2026-09-10)

After the five design lenses, Devil’s Advocate patched `requirements.md` so implementers cannot follow a weaker SHALL:

- Overlay `name: aimf_p0` (base file is `ai_mesh_firewall`); no `compose build --build`.
- Policy oracle = `/v1/observability` org RAM, not Redis GET / `/health.policy_count`.
- Restart-or-poll after seed; post-run re-GET Redis **and** RAM; empty glob includes `llm:model_configs*` / `kill_switch:*`.
- Counted_Sample: HTTP 200, stub id, `usage.completion_tokens`, no `scan_only`/flag/rewrite; driver 1×1; no `run()`.
