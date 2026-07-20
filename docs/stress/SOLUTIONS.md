# AI Mesh Firewall — Defensive Approaches (R3)

**Reference only.** Approaches distilled from studying how mature OSS guardrails / LLM firewalls
(Azure Prompt Shield, ProtectAI/LLM-Guard, NeMo-Guardrails, Rebuff, Vigil, garak, the 2025 evasion
literature) *approach* these problems. **No code is vendored, copied, imported, installed, or run** —
every solution here is implemented natively in this repo's owned chat modules (see R4). Each approach
is tied to the gap register in `ATTACK_LANDSCAPE.md` (G1–G14).

## Core principle
> "Detection fails where comprehension succeeds." Guardrails match surface tokens; the model reconstructs
> meaning from context. The robust fix is a **layered canonical-form pipeline applied BEFORE any
> keyword/PII/classifier check**, plus scanning **both** the raw and normalized form (a large raw-vs-
> normalized delta is itself an alarm). This repo's Tier-0.5 already does this for *attack* patterns; the
> asymmetry (PII/secret detectors run raw-only) is exactly G1/G2/G4.

## A. Canonicalize-before-match (fixes G1, G3, G4, G-carveout, partially G14)
- **Ordering that mature stacks use:** strip invisibles → NFKC → fold combining marks → fold confusables →
  fold separators → (bounded) decode transports → scan. Applied once, it neutralizes infinite invisible
  variants against a canonical corpus.
- **Invisible/format chars:** strip Unicode `Cf` category (zero-width U+200B–200D, U+2060, U+FEFF, soft
  hyphen U+00AD, bidi U+202A–202E/U+2066–2069, Tag block U+E0000–E007F). Caveat noted in OSS: don't blanket-
  delete ZWJ/ZWNJ inside legitimate Indic/emoji sequences — normalize-for-scanning while keeping the
  original for delivery.
- **Compatibility forms:** NFKC folds fullwidth (U+FF01–FF5E), math-alphanumerics, circled/enclosed. NFKC
  does **not** fold cross-script confusables → add a confusable/skeleton fold (UTS-39 style: Cyrillic/Greek/
  Cherokee → Latin) and/or **mixed-script flagging**.
- **Separators:** NFKC does **not** fold U+2011 non-breaking hyphen to ASCII `-`; explicitly fold the
  Unicode dash set (U+2010–2015, U+2212, U+FE58/63, U+FF0D) → `-` and `Zs` spaces → ` ` (this repo's
  verified SSN-U+2011 leak). Diacritics: NFKD + strip `Mn`.
- **Span-back masking (key implementation choice):** to actually remove the secret from **egress bytes**
  (not just detect it), canonicalize with a **position-preserving index map** (1→1 substitutions + 1→0
  removals only, *avoid* length-changing NFKC expansions on the redaction path) so a match found in the
  canonical form can be mapped back and masked in the original. Mature DLP stacks map normalized offsets
  back to source offsets for exactly this reason.
- **De-spaced/segmented candidate (G3):** merge short 2–3 char chunks and re-run injection signatures; the
  current destructive `findall([a-zA-Z]+)` keeps chunk boundaries — add a spacing-collapsed candidate.

## B. Bounded decode-then-rescan (fixes G2, prompt laundering)
- Detect encoded substrings by charset+entropy+length heuristics (Base64 `[A-Za-z0-9+/=]{16,}`, hex pairs,
  Base32, ROT13), **decode, and re-run PII/secret + injection detection on the plaintext**; the "Mixture of
  Encodings" defense line decodes across encodings. Recurse to a **bounded depth** (attackers nest layers)
  with a **decode-bomb guard** (cap decoded size / recursion). On a hit, mask the *encoded blob* in egress
  and/or flag "decode-and-execute + high-entropy blob" as its own signal.

## C. ReDoS-safe matching (fixes G12; guards A/B)
- Prefer linear-time constructs: bounded quantifiers over unbounded `.*`, atomic-ish designs, no
  nested/overlapping quantifiers on attacker-controlled spans. Enforce **input length caps** before regex,
  **per-scan wall-clock budgets**, and **decode-depth caps**. (RE2/linear engines are the industry answer;
  here we keep Python `re` but bound patterns + input, as `policy_engine` already does — extend the same to
  `context_guard` and `leakage_detector`.) The canonicalizer itself must be linear char-scan, length-capped.

## D. Enforcement-consistency & precedence (fixes G5, G9, G10)
- A verdict must **enforce or fail-closed** — never compute `redact`/`rewrite` and forward raw. If a redact
  rule lacks a concrete redaction, fall back to a placeholder mask or block (don't no-op). Severity-order
  multi-category decisions (secret `block` must not be pre-empted by a toxicity `flag`). For semantic-only
  PII the regex can't mask, prefer **typed-placeholder tokenization** or block over a relabeled no-op.

## E. Stateful multi-turn tracking (fixes G6)
- Per-session context: declared purpose, rolling summary, per-turn risk history, embedding drift,
  cumulative decayed risk. Detectors: monotonic-escalation/CUSUM (crescendo), fabricated-dialogue density
  (many-shot), assistant-output poisoning acknowledgments (echo-chamber), last-N-turn recombine-and-rescore
  (split payloads), persisted persona/policy-tamper flags (DAN/skeleton-key/policy-puppetry).

## F. Output-side egress channels (fixes G13, canary)
- Parse Markdown/HTML in model output; enforce an **image/link egress allowlist**; percent/IDN-decode URLs
  and scan query params for secrets; scan **tool-call argument** structural fields, not just descriptions.
  Scan responses for planted canaries **after** the same canonicalization so encoded/paraphrased canaries
  still match; combine with n-gram semantic leakage accumulation.

## Mapping to R4 work (owned chat modules only)
| Approach | Gap(s) | File(s) |
|---|---|---|
| A canonicalize + span-back mask | G1, G3, G4 | `patterns.py`, `scanner.py` |
| B bounded decode-then-rescan | G2 | `patterns.py`, `scanner.py` |
| C ReDoS-safe + caps | G12 | `context_guard.py`, `patterns.py` |
| D enforce-or-fail-closed / precedence | G5, G9, G10 | `policy_engine.py`, `context_guard.py`, `output_guard.py`, `typed_placeholder_redactor.py` |
| E stateful multi-turn | G6 | `scanner.py` (+ minimal claimed `main.py` session wiring) |
| F output egress channels | G13 | `output_guard.py` |
