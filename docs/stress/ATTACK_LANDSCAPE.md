# AI Mesh Firewall — LLM Attack Landscape (2026)

**Program:** chat-pipeline adversarial stress (`claude-ralph-stress`) · **Status:** R1 research · **Floor:** the 9 frozen golden cases stay green.

This document maps the **current (2026) LLM-firewall attack landscape** onto **this repository's actual chat pipeline**. It is not a generic survey: every attack family is tied to the concrete gateway stage that should stop it, the code path that does (or does not), and — where an autonomous audit confirmed it — a **verified** bypass with `file:line` evidence. Part C is the prioritized **gap register** that R2 encodes as xfail golden cases and R4 fixes inside the owned chat modules.

Method: an in-process code audit (5 parallel readers over `main.py` chat path, `scanner.py`/`patterns.py`, `policy_engine.py`/`context_guard.py`, `output_guard.py`/`secure_streaming.py`, `pipeline_trace.py`) + 4 parallel web-research streams (OWASP LLM Top-10 2025, obfuscation/encoding, multi-turn jailbreaks, exfil/ReDoS). Sources at the end. **OSS studied for ideas only — no code vendored.**

---

## Part A — The pipeline under test

### A.1 Ordered chat firewall stages (`proxy_chat`, `main.py:4255`; `/v1/responses` reuses the same firewall via `_dispatch_chat_internally` `main.py:8282`)

1. **input validation + normalization** (`main.py:4293-4720`) — `_strip_lone_surrogates`, `normalize_openai_chat_request`, message/token bound guards (`MAX_MESSAGES=200`, per-message cap).
2. **auth + org config** (`~4739`) — `request.state.auth_context`, `CONFIG_SYNC.get_config`.
3. **threat-intel + kill-switch** (`~4965-5195`).
4. **rate-limit** (`~5367-5555`) → 429.
5. **prompt extraction** (`~5561`) — `_extract_prompt_from_messages` → `effective_prompt` (concatenated multi-turn blob).
6. **deterministic policy engine FIRST** (`~5745-5965`) — `policy_engine.evaluate` → block(403)/redact/rewrite/model_downgrade. Runs before scan so `block` short-circuits Tier-2 cost.
7. **Tier-1 + Tier-2 input scan** (`~5974-6400`) — `scanner.scan_prompt[_with_tier2]`; block→403; PII/secret→`redact_pii` mutates prompt; **fail-CLOSED block on redaction no-op ~6321**.
8. **routing / model-select** (`~6602`) — ownership gate `6620`, `adjudicate_model_selection`, isolation reroute.
9. **provider call** (`7093-7178`) — tenant gate, circuit breaker, `LLM_ROUTER.acompletion(body, redacted_prompt)`.
10. **output guard** (`7250-7660`) — `OUTPUT_GUARD.inspect` over `content + reasoning_content + tool_calls` → block/redact/rewrite/flag.
11. **post-LLM policy** (`7690-7860`).
12. **zeroshield meta + trace + telemetry** (`7916-8215`) — `build_pipeline_trace`, `_emit_telemetry`, `_redact_for_client_response`.

**Streaming** replaces 9–12 with `SecureStreamingResponse` (`secure_streaming.py:273`): SSE buffered, `OUTPUT_GUARD.inspect` per flush, tokens masked/withheld before yield; `rewrite`→`block` coercion mid-stream.

### A.2 Enforcement precedence (verified)

- **Input scanner** (`scanner._scan_prompt_sync`): first matching tier/category wins, short-circuits: `length>10000 dos` → repetition dos → Tier-1 `ATTACK_PATTERNS` (raw) → Tier-0.5 (deobfuscated) → Tier-1.5 fuzzy → PII redact → secret redact → toxicity block.
- **Policy engine** `ACTION_ORDER` (`policy_engine.py:25`): `block(5) > redact(4) > rewrite(3) > model_downgrade(2) > monitor(1) > allow(0)`; highest across all matched rules wins.
- **Output guard** `ACTION_PRIORITY` (`output_guard.py:45`): `block(4) > redact(3) > rewrite(2) > flag(1) > allow(0)`; `block→redact` downgrade for redactable categories.
- **Global:** secrets always redacted regardless of org PII toggle. **fail-CLOSED** on policy-eval unavailable, tier2-strict breaker-open, and unmaskable flagged-PII no-op; **fail-OPEN** when output guard / Tier-2 degraded (response passes UNSCANNED, telemetry only). ← see G8.

### A.3 Egress = the only truth

- **Prompt→provider:** in-handler `effective_prompt` is only a *signal*; the real wire bytes are re-redacted in `LLMRouter._apply_redaction` (`llm_router.py:626`) via `_redact_text_with_backstop` (`patterns.redact_all` + fail-closed digit backstop). **System-role message content is left UNREDACTED** (`llm_router.py:651`) ← see G-system.
- **Response→client:** `_set_completion_response_text` (`main.py:3034`) overwrites every choice; `_neutralize_secondary_output_channels` blanks `reasoning_content`/`tool_calls`.
- **The scrubber is `patterns.redact_all` — the SAME raw regex catalogue as detection, with NO normalization.** Therefore any obfuscated PII/secret that evades `detect_pii` **also** evades `redact_all` → neither detected nor masked → egresses in cleartext. This coupling is the root of the highest-severity leaks (G1, G2, G4).

---

## Part B — Attack taxonomy (OWASP LLM Top-10 2025, mapped to this system)

Format per family: **technique** → why it bypasses naive filters → canonicalization/detection that catches it → **this system today** (defense + gap, `file:line`).

### B1 · Direct prompt injection / jailbreak — LLM01
Instruction override ("disregard all prior instructions"), roleplay/DAN persona, virtualization/hypothetical framing, adversarial (GCG) suffix.
- **Detection:** intent classifier + instruction-hierarchy tagging; perplexity/entropy anomaly for suffixes; output-side refusal reinforcement.
- **This system:** `ATTACK_PATTERNS` regex (`scanner.py:118-213`) + fuzzy anchors (Tier-1.5) + Tier-2 Bedrock. **Gap:** literal/fuzzy signatures are reworded/obfuscated away (see B3); the **policy #5 quoting carve-out** (`_is_explanatory_mention`, `scanner.py:867`) downgrades an injection block when the payload is wrapped in quotes/"the phrase" within a 30-char lead-in → G-carveout.

### B2 · Indirect / RAG-borne injection — LLM01 + LLM08
Instructions planted in retrieved docs/HTML comments/metadata; the model treats DATA as INSTRUCTIONS.
- **Detection:** run the SAME normalization+injection scan on every retrieved chunk; instruction/data segregation; strip HTML comments/hidden layers at ingest.
- **This system:** `RAG_POISONING_PATTERNS` + `context_guard` (`HIDDEN_INSTRUCTION_PATTERNS`, `INDIRECT_INJECTION_PATTERNS`). **Gap:** `context_guard` *detects* zero-width runs but never *strips* them before running injection regex (`context_guard.py:145`); single-doc precedence inversion (toxicity `flag` returns before secret `block`, `context_guard.py:270` vs `297`) → credential at ingest not blocked (G9); ingest enforces only `action=='block'` (`vector_routes.py:717`).

### B3 · Obfuscation & encoding — LLM01 (the dominant evasion class in 2025 empirical studies)
| Sub-technique | Shape (abstract) | Canonical fix |
|---|---|---|
| NFKC-foldable (fullwidth `ＫＥＹ`, math-alnum `𝐊𝐄𝐘`) | codepoints render like ASCII | NFKC before match |
| Cross-script homoglyph (Cyrillic `а`, Greek `Ο`) | NFKC does **not** fold these | TR39 `skeleton()` / mixed-script flag |
| Zero-width / soft-hyphen (U+200B/C/D, U+2060, U+FEFF, U+00AD) | split keyword invisibly | strip Cf-category, rejoin, rescan |
| Bidi override (U+202A-E, U+2066-9) | display ≠ logical order | strip bidi, scan logical order |
| Unicode Tag block (U+E0000-E007F) | invisible ASCII carrier (~90% ASR) | decode tags→ASCII, rescan |
| Emoji variation-selector (U+FE00-FE0F, U+E0100+) | 256-value byte carrier (~100% ASR) | strip VS, decode, rescan |
| Diacritic/combining (Zalgo) | `ìgnòrè` | NFKD + strip Mn marks |
| Leetspeak (`1gn0r3`) | ASCII, NFKC no-op | leet-fold + fuzzy |
| Base64/base32/hex/ROT13/Morse/Braille | opaque blob, model self-decodes | recursive decode→rescan (bounded depth) |
| Whitespace/comment/markup (`K E Y`, `KEY/*x*/WORD`) | visible separators break contiguity | collapse Zs, strip comments/entities |
| Multi-layer nested (zw→base64→homoglyph) | single-pass decoders stop early | normalize→decode to **fixed point** |

- **This system (asymmetric normalization — the core weakness):** `scanner._deobfuscate_text` (`scanner.py:953`) DOES normalize (zero-width+bidi strip, NFKC, NFD+Mn strip, Cyrillic/Greek homoglyph fold, leet, bounded base64/hex/ROT13) — **but only the Tier-1 attack/RAG rescan uses it.** `detect_pii`/`detect_secrets`/`redact_all`/`_check_toxicity` run on **RAW text only** (`scanner.py:789/800/811`, `patterns.py:413/460/621`).
  - **VERIFIED bypass (G1):** SSN with U+2011 non-breaking hyphen, email with fullwidth `＠`, API key with embedded ZWSP → **action=allow, no redaction, egresses cleartext.**
  - **VERIFIED bypass (G2):** base64 of `123-45-6789` → allow (decode variants feed only attack/RAG rescan, never PII/secret).
  - Homoglyph table incomplete (missing Cherokee/Armenian/IPA `U+0261`); leet map incomplete (`8→b`, `2→z`, `|→l`, `()→o`); `findall([a-zA-Z]+)` is **destructive** — drops digits/punctuation so obfuscated ChatML/`<|im_start|>`/`new instructions:` never match the deobfuscated buffer (G3); decode is single-depth (double-base64 evades).

### B4 · Multi-turn & advanced jailbreaks — LLM01
Crescendo (gradual escalation citing the model's own answers), many-shot (100s of fake compliant demos in one context), Echo-Chamber (context poisoning), Skeleton-Key (rule-augmentation), Policy-Puppetry (structured-config injection), refusal-suppression (DSN), split-across-turns.
- **Detection:** **stateful** per-session risk (cumulative + decay), monotonic-escalation/CUSUM, embedding drift vs declared purpose, last-N-turn recombination-and-rescore, structured-blob parsing, assistant-output scanning for poisoning acknowledgments, persisted persona/policy-tamper flags.
- **This system:** scanner is **stateless per-prompt** (10 000-char cap on the concatenated blob). Many-shot has partial incidental coverage (blob length/repetition), but crescendo/echo-chamber/split-across-turns/skeleton-key are **not** caught (G6). No session risk store.

### B5 · Sensitive-info disclosure & exfiltration channels — LLM02 / LLM07 / LLM08
- **System-prompt leakage** (LLM07): "repeat everything above" → output-side canary/fingerprint scan; never store secrets in system prompt.
- **Markdown-image / hyperlink auto-fetch** (EchoLeak/CamoLeak class): model emits `![](https://sink/?d=<b64 secret>)`; client auto-renders → zero-click GET. **This system:** output guard folds text/reasoning/tool_calls but does **not** parse markdown URLs or enforce an image/link egress allowlist (G13).
- **Embedding-request exfil** (LLM08): plaintext PII vectorized+stored; inversion recovers 50-70%. **This system:** upsert-scan path exists (`test_e11_upsert_vector_guard`) but same raw-normalization coupling applies.
- **Tool-argument / MCP exfil** (LLM06): secret encoded into a tool arg. **This system:** only tool *descriptions* are redacted (`llm_router.py:288`), not tool-call argument structural fields (G13).
- **Canary leakage:** `leakage_detector` n-gram fingerprints exist; must scan **after** canonicalization so encoded/paraphrased canaries still match.
- **Tier-2 semantic redact is a byte no-op (G10):** a guard-model block on `pii/pci/phi/secret/credential` is downgraded to `redact` (`output_guard.py:384`), but `redact_all` has no regex for free-text names / non-standard card & ID layouts, so `redacted==original`. The code **honestly relabels to `flag`** (`main.py:7408`) — no phantom-redaction lie — **but the value still egresses verbatim.** Tier-2 `redact`/`monitor` are mapped to `flag` (`scanner.py:1613`), so Tier-2 can never drive a surgical mask.

### B6 · ReDoS / algorithmic DoS — LLM10 (Unbounded Consumption)
- **This system:** email pattern was a confirmed ReDoS (500 KB field hung ~18 min), now bounded → linear (`patterns.py:73`); `policy_engine` wraps arbitrary bundle regex in a 1.0 s / 100 k-char budget (`policy_engine.py:85-136`) **but** the daemon worker keeps running after timeout (thread pile-up). **Gaps:** `context_guard` has **NO** per-match timeout (`context_guard.py:246`); `leakage_detector` builds 3/4/5-gram sets over the **entire untrimmed** output (`leakage_detector.py:70`); `redact_all` reruns the full catalogue with no length cap on the output path (G12). Amplification: the deobfuscation second pass re-normalizes + re-scans every decoded variant.

### B-trace · Honest-trace integrity (feeds R6)
`stages[].action` is re-derived post-hoc from string diffs (`pipeline_trace.py:439`), so an **idempotent/equal-length redaction shows `allow`** though a redaction was enforced; fabricated per-stage latencies (`pipeline_trace.py:456`). **`OutputPipelineTimeline.jsx` (the task-named card) does NOT consume `stages[]`** — it fabricates 6 stages colored from one global `event.action` (`jsx:104-195`); the honest per-stage consumer is `frontend/src/components/simulator/StageTimeline.jsx`. (G11 — R6 target.)

---

## Part C — Confirmed gap register (prioritized → R2 corpus + R4 fixes)

Severity: **P0** proven leak, **P1** enforcement/correctness, **P2** hardening. "Owned?" = fixable inside `claude-ralph-stress` chat modules.

| ID | Family | Severity | Verified? | Current behavior (evidence) | Expected | R4 fix area (owned) |
|----|--------|----------|-----------|------------------------------|----------|---------------------|
| **G1** | Unicode/zero-width/homoglyph PII+secret | **P0** | ✅ | fullwidth `＠` email / U+2011 SSN / ZWSP-split API key → `allow`, egress raw (`scanner.py:789/800`, `patterns.py:621`) | detect+redact after canonicalization | `patterns.py` + `scanner.py`: normalize before `detect_pii`/`detect_secrets`/`redact_all` |
| **G2** | Base64/hex-encoded PII+secret | **P0** | ✅ | base64(SSN) → `allow` (decode feeds only attack rescan, `scanner.py:338`) | decode→rescan PII/secret (bounded depth) | `scanner.py` decode variants into PII/secret detectors |
| **G4** | Output-side obfuscated secret | **P0** | ▲ | `redact_all` output path has no deobfuscation (`patterns.py:621`) | mask obfuscated secret in output bytes | `patterns.py`/`output_guard.py` |
| **G10** | Tier-2 semantic-only PII/secret | **P1** | ▲ | downgraded redact = byte no-op, relabeled flag, egress raw (`output_guard.py:384`) | mask via placeholder or block | `output_guard.py`/`typed_placeholder_redactor.py` |
| **G5** | Computed-but-not-enforced policy | **P1** | ▲ | `redact` w/o `redaction_config` → forwarded raw (`policy_engine.py:362` + `main.py:1079`); `rewrite` no substitution | verdict must enforce or fail-closed | `policy_engine.py` |
| **G6** | Multi-turn (crescendo/echo/split) | **P1** | ▲ | scanner stateless per-prompt | session risk state + recombine-rescore | `scanner.py` (+ minimal `main.py` session wiring, claimed) |
| **G3** | Chunk-split injection | **P1** | ✅ | `ig no re all previous instructions` → `allow` | catch via de-spaced/segmented rescan | `scanner.py` deobfuscation |
| **G8** | Tier-2 output-guard fail-open | **P1** | ▲ | degraded → unscanned egress (`main.py:7289`) | fail-closed option / hold-stream | `output_guard.py`/`scanner.py` |
| **G9** | context_guard precedence inversion | **P1** | ▲ | toxicity `flag` before secret `block` (`context_guard.py:270`) | severity-ordered decision | `context_guard.py` |
| **G13** | Markdown/link + tool-arg exfil | **P2** | ▲ | URL query + tool-arg fields unscanned | parse+allowlist+scan URLs/args | `output_guard.py` |
| **G-carveout** | Quoting carve-out abuse | **P2** | ▲ | quoted injection downgraded (`scanner.py:867`) | tighten carve-out | `scanner.py` |
| **G12** | ReDoS/DoS (context_guard, leakage, redact_all) | **P2** | ▲ | no timeout/length caps (`context_guard.py:246`, `leakage_detector.py:70`) | linear-time + caps + timeouts | owned modules |
| **G-system** | System-role message unredacted | **P2** | ▲ | `_apply_redaction` skips system slot (`llm_router.py:651`) | scan system content too | `llm_router.py` (claim before edit) |
| **G14** | Multilingual/low-resource | **P2** | ▲ | English-centric patterns | translate-then-scan or multilingual | `scanner.py` (best-effort) |
| **G11** | Trace honesty + card | **P2** | ✅ | idempotent redaction shows `allow`; `OutputPipelineTimeline` not stage-driven | per-stage truth from a ground-truth flag | R6: `OutputPipelineTimeline.jsx` (+ `StageTimeline.jsx`) |

✅ = agent-verified evasion in this codebase · ▲ = code-path confirmed, to be reproduced by R2 harness.

**R4 headline fix (unblocks G1/G2/G4 together):** introduce a single `canonicalize_for_detection(text)` in the owned scanner/patterns layer (NFKC → strip Cf zero-width/bidi → NFKD+strip Mn → TR39-style confusable fold → optional bounded transport-decode) and run **both** the raw and canonical forms through `detect_pii`/`detect_secrets`/`redact_all`. Because detection and the scrubber share the same catalogue, fixing detection fixes masking. Keep it ReDoS-safe (linear, length-capped) and never weaken the 9 frozen cases.

---

## Part D — Corpus design principles (for R2)

1. **Egress bytes are the only truth.** A case passes only if the captured outbound bytes (to provider) and inbound bytes (to client) reflect the claimed verdict. Cross-check with an independent oracle (`aidefence_scan`/`aidefence_has_pii`) so detection under test isn't graded by itself.
2. **Scan raw AND normalized;** a large raw-vs-normalized delta is itself an alarm.
3. **Decode to a fixed point** (normalize→decode→rescan until stable), depth-bounded to avoid decode bombs.
4. **Multi-turn cases are SEQUENCES,** replayed in order through a stateful pipeline; assert both peak-turn and cumulative-trajectory verdicts.
5. **Every confirmed failure → one xfail golden case** with `{reproduction, stages[], expected, actual}`; freeze on green. Never weaken the 9.
6. **No secret ever written to a fixture/log/snapshot;** use synthetic PII and the abstract `KEYWORD`/placeholder shapes above.

---

## Sources (2025–2026)

OWASP GenAI LLM Top-10 2025 (LLM01 Prompt Injection, LLM02 Sensitive Info Disclosure, LLM07 System-Prompt Leakage, LLM08 Vector & Embedding Weaknesses, LLM10 Unbounded Consumption); OWASP LLM Prompt-Injection Prevention Cheat Sheet. Empirical evasion: *Bypassing LLM Guardrails* (arXiv 2504.11168, per-technique ASRs); *Defense via Mixture of Encodings* (arXiv 2504.07467); confusables/TR39 skeleton (paultendo); emoji variation-selector smuggling (Paul Butler 2025); Unicode Tag smuggling (garak, Goodside 2024); invisible-char/bidi evasion (Mindgard, Knostic, ctx-guard); Augustus fuzzer (15+ encodings). Multi-turn: Crescendo (Microsoft, USENIX Sec'25); Many-shot (Anthropic 2024); Skeleton Key (Microsoft 2024); Policy Puppetry (HiddenLayer 2025); Echo Chamber (NeuralTrust 2025); DSN refusal-suppression (ACL Findings 2025); low-resource-language jailbreak (Yong et al.). Exfil: EchoLeak CVE-2025-32711, CamoLeak; embedding inversion (ALGEN 2025); VectorSmuggle/RAGPoison; USENIX Sec'25 RAG poisoning. Full URL list in the R1 workflow journal.
