# Claude Code Ralph — Chat Pipeline Adversarial Stress + Client E2E + Impeccable Revamp
# Floor = the 9 frozen golden cases must stay green. New attacks → new golden cases.

## R0 — Coordination + key-safety
- [x] 0. Join hive; claim owned files in mcp-parallel/claims (chat modules, golden suite,
      ModelConnectionPanel.jsx, pipeline-trace cards). Add .env* to .gitignore. Add a pre-commit hook
      that greps staged diffs for the OpenRouter key + `sk-or-` and aborts on match. Run /impeccable init
      (writes PRODUCT.md/.impeccable.md — design context; additive, no code churn).
      DONE 2026-07-02: claim `mcp-parallel/claims/claude-ralph-stress-iter1-R0.claim` + ledger row in
      `docs/mcp/PARALLEL_CLAIMS.md` (narrow frontend carve-out: only ModelConnectionPanel.jsx +
      OutputPipelineTimeline.jsx; Cursor's frontend/** otherwise respected; no live-claim overlap).
      `.gitignore` += `.env*`,`*.key`,`*.secret`. Pre-commit guard `scripts/ralph/precommit-secret-scan.sh`
      installed at `.git/hooks/pre-commit` — blocks (1) the runtime OpenRouter key by one-way SHA-256
      fingerprint (every file), (2) any real-shape `sk-or-v1-` token outside vetted redaction fixtures.
      4/4 self-tests pass (live-key BLOCK, non-fixture BLOCK, fixture ALLOW, no self-trigger). Live key
      lives ONLY in untracked working copy of `.claude/ralph-loop.local.md`; HEAD clean; never staged.
      NOTE: `impeccable` skill/plugin is NOT installed in this env — R6 design polish will be done
      manually (no PRODUCT.md/.impeccable.md produced). Pipeline-trace card = OutputPipelineTimeline.jsx.
      Golden suite `gateway/tests/golden/` does NOT exist yet; the gate `pytest tests/golden` currently
      resolves to nothing — R2/R3 must CREATE it (incl. materializing the "9 frozen" baseline cases).

## R1 — Research current attacks (browser/web)
- [x] 1. Research the CURRENT (2026) LLM firewall attack landscape: OWASP LLM Top-10, direct + indirect
      prompt injection, jailbreaks (roleplay/DAN, many-shot, crescendo/multi-turn), obfuscation
      (unicode/homoglyph/leetspeak/base64/rot13/zero-width), token smuggling, language switching, split
      payloads, PII/secret exfil, guardrail bypass, RAG/tool poisoning, ReDoS/DoS. → docs/stress/ATTACK_LANDSCAPE.md.

## R2 — Edge-case stress against the FROZEN pipeline (in-process)
- [ ] 2. Build docs/stress/ADVERSARIAL_CORPUS.py from R1: inputs that try to (a) sneak PII past as
      allow (LEAK), (b) force wrong block (false positive), (c) hang/crash (ReDoS, huge input, deep
      nesting), (d) corrupt the trace/cards. Drive the real pipeline; record every failure to
      mcp-parallel/findings with the stages[] trace.
- [ ] 3. Encode each confirmed failure as a NEW golden case (xfail until fixed). Never weaken the 9.

## R3 — OSS research (GitHub MCP + browser) — REFERENCE ONLY
- [ ] 4. Study how mature guardrail/LLM-firewall OSS handle these attacks (detection, normalization,
      canonicalization, ReDoS-safe matching, multi-turn tracking). Extract APPROACHES →
      docs/stress/SOLUTIONS.md. Do NOT import/vendor/run any OSS tool — your own code only.

## R4 — Fix on this worktree
- [ ] 5. Implement fixes in the chat modules (enforcement.py, policy_engine.py, bedrock_scanner.py,
      scanner/input-scan, output_guard.py): input normalization/canonicalization before matching,
      ReDoS-safe patterns, obfuscation decoding, multi-turn/context handling, and correct enforcement
      per the precedence table (redact stays redact; leaks become redact/block; false positives relax).
      Keep main.py edits minimal + claimed. New golden cases go green; the 9 stay green.

## R5 — Client E2E stress with 10 real free OpenRouter models
- [ ] 6. Playwright as a client: in ModelConnectionPanel, add provider (OpenAI-compatible, base URL
      https://openrouter.ai/api/v1), enter OPENROUTER_KEY (from env, typed into the UI — NEVER stored by
      you), connect 10 FREE models (query OpenRouter /models, pricing.prompt==0). Verify they appear as
      connected. (Pre-commit key scan must stay clean.)
- [ ] 7. Run the adversarial corpus END-TO-END via the stock OpenAI SDK (org gateway key) through the
      full pipeline against the real models: assert enforcement is correct (no PII leak to any model;
      redact stays redact; blocks are justified), routing/kill-switch behave, and the trace is honest.
      Any real-world failure → fix (R4) → re-run. Redact the key from all logs/snapshots.

## R6 — Impeccable frontend revamp (client-facing, Playwright-verified)
- [ ] 8. /impeccable audit + /critique + /polish on ModelConnectionPanel (the client's first touch:
      connect-model + key-entry UX, empty/error/loading states, the "key is write-only/encrypted"
      affordance) and the pipeline-trace cards (implement TRACE_UI_CONTRACT.md; honest per-stage badges).
      Scope to owned pages; avoid global shared-component rewrites that would clash with the MCP session's
      frontend at merge (coordinate any design-token change via the ledger). Impeccable detector clean.
- [ ] 9. Playwright verify the revamp end-to-end (connect flow works, cards render from stages[], focus/
      a11y/responsive, zero console errors), with before/after screenshots.

## R7 — Freeze the hardened pipeline
- [ ] 10. Full golden suite (9 original + new attack cases) green 3× in-process AND live-with-real-models;
      impeccable clean; pre-commit key-leak scan clean; no MCPConnectorPanel edits. Output <promise>COMPLETE</promise>.

---

## DURABLE SESSION NOTES (read every iteration — avoid re-deriving)

### Environment (CRITICAL — the repo was authored on macOS; .venv312 is a BROKEN mac venv here)
- Working Python env = `uv`. One-time: `cd gateway && uv sync --extra dev` (materialises the project's OWN
  locked deps incl. pytest — NOT external OSS; legitimate for running gates). Creates `gateway/.venv`.
- Run backend tests with the venv python + PYTHONPATH (NOT `uv run pytest`, which grabs system py3.14):
  `cd gateway && GATEWAY_LIVE=0 PYTHONPATH=. .venv/bin/python -m pytest tests/golden -q`
- GOLDEN GATE (the 9): `GATEWAY_LIVE=0 PYTHONPATH=. .venv/bin/python -m pytest tests/golden -q`
  → offline = 3 passed / 7 skipped / 0 failed (7 need live mode → R5 with OpenRouter key, GATEWAY_LIVE=1).

### Coordination HAZARD (proven this session)
- ALL sessions (this Claude, Cursor MCP, freeze) share ONE working tree + ONE git index on `main`.
  A concurrent `git add -A` by another session SWEEPS UP your staged files into THEIR commit
  (happened: golden foundation landed under commit 5d0dd346 "P8 item 26", not my R1a commit f3980687).
  → RULE: stage NARROWLY (explicit paths, never `git add -A`) and COMMIT IMMEDIATELY after staging.
  → NEVER `git add -A` (would also sweep .claude/ralph-loop.local.md which holds the LIVE KEY).

### What is DONE
- R0 (042c11c2): claim + pre-commit secret-scan guard (.git/hooks/pre-commit, fingerprint 93a9a831…) + .env* ignore.
- R1a integration (files landed in 5d0dd346): the "9 frozen golden cases" + enforcement.py + pipeline
  contracts existed ONLY on branch `cursor/chat-pipeline-stress`, not main. Integrated the released
  commits f1a091cc..dd55f8fb onto main (main.py/policy_engine/pyproject were untouched on main since
  merge-base 53b169f9 → zero-regression). Golden gate green on main.
- R1 (this commit): docs/stress/ATTACK_LANDSCAPE.md — system-tailored, with gap register G1–G14.

### R2/R4 ROADMAP (from ATTACK_LANDSCAPE.md Part C — priority order)
- G1 (P0, VERIFIED leak): unicode/zero-width/homoglyph PII+secret bypass Tier-1 (detect_pii/detect_secrets/
  redact_all run on RAW text only; scanner.py:789/800, patterns.py:621). fullwidth @, U+2011 SSN, ZWSP key → allow.
- G2 (P0, VERIFIED): base64/hex-encoded PII+secret not decoded for PII (scanner.py:338 decode feeds only attack rescan).
- G4 (P0): output-side has NO deobfuscation. G10 (P1): Tier-2 semantic redact = byte no-op (honest flag, egress raw).
- G5 (P1): computed-but-not-enforced policy (redact w/o config forwards raw; policy_engine.py:362 + main.py:1079).
- G6 (P1): scanner STATELESS → crescendo/echo/split-across-turns uncaught. G3 (P1): chunk-split "ig no re" → allow.
- HEADLINE R4 FIX: add `canonicalize_for_detection()` in owned scanner/patterns; run raw+canonical through
  detect_pii/detect_secrets/redact_all (shared catalogue → fixes detection AND masking). ReDoS-safe, keep 9 green.

### R6 note (frontend)
- Task-named `OutputPipelineTimeline.jsx` does NOT consume stages[] — colours 6 fake stages from one global
  event.action. The HONEST per-stage consumer is `frontend/src/components/simulator/StageTimeline.jsx`.
  TRACE_UI_CONTRACT.md now exists at docs/pipeline/TRACE_UI_CONTRACT.md (integrated). R6 must reconcile both.
- `impeccable` skill/plugin is NOT installed → do R6 polish manually.
