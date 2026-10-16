# Ledger note: injection and command demoted from terminal block to signal (§10.2.2)

This is the authoritative, reviewer-readable record of a deliberate **§10.2.2 behaviour change**:
GW09's deterministic detectors no longer let an injection or command pattern terminally block a
request. The family emits a signal `Finding` only; the plan/resolver decides the action. The GW09
runbook card (§10.9, `## GW09 — Implement deterministic detectors with byte-verified redaction`)
requires this ledger entry because the change is a signed, recorded trade against v1's behaviour.

It is co-located under the `detect/deterministic` package it documents, mirroring the B8
declared-failure-behaviour doc convention (`gateway_v2/state_control/docs/declared-failure-behaviour.md`).

## 1. The defect

v1's `ATTACK_PATTERNS["command_injection"]` contained a bare backtick-pair pattern — `` `[^`]+` `` —
that matched **any** inline-code backtick pair and **terminally blocked** the request. Because
`command_injection` sat outside the explanatory carve-out, the block was terminal: there was no
downstream opportunity for the plan or resolver to soften it.

The measured effect on developer traffic was total: **5 of 5 benign inline-code prompts were
blocked**. Any prompt quoting a snippet with backticks — the normal way a developer asks about code —
was rejected. The developer-traffic false-positive rate on this family was effectively **1.0**.

## 2. The change

Injection and command patterns are **demoted from a terminal block to a signal**:

- A detector emits a `Finding` only. The injection/command family carries the categories
  `PROMPT_INJECTION`, `JAILBREAK`, and `COMMAND`. It never terminally blocks.
- The **plan/resolver (GW07)** decides the action from the organization's rules. The detect layer no
  longer owns disposition for this family.
- In the shipped GW09 ruleset (`detect/deterministic/patterns.py`), these patterns are marked
  `PatternKind.SIGNAL` (as opposed to `PatternKind.REDACTABLE`): a signal span is never a redaction
  target and never forces a block.
- The **bare backtick-pair pattern is removed.** The command family now matches only explicit
  shell-injection constructs: command substitution `$(...)`, destructive command chains such as
  `; rm -rf`, and pipe-to-shell (`... | sh`). A benign inline `` `code` `` span matches none of them.

## 3. Why it is a §10.2.2 behaviour change

v2 **deliberately diverges** from v1's terminal-block behaviour on this family. v1 treated an
injection/command match as a hard stop; v2 treats it as evidence the resolver weighs against the
org's rules. This is not a bug fix that restores an intended behaviour — it is a change of contract,
so it is logged here as the signed, recorded trade the GW09 card requires.

## 4. Evidence and metric

- **v1 (before):** developer-traffic FPR on the bare-backtick family was effectively **1.0** — 5 of 5
  benign inline-code prompts blocked.
- **v2 (target):** **zero terminal blocks** on that family. The family is signal-only; whatever
  action follows is the resolver's, not detect's.
- Per-family **recall and FPR** are scored from the GW09 local Fixture_Corpus (the C3 score, exercised
  by LGW09-6), with the Developer_Traffic_Family FPR reported separately. Scores are outcomes read
  from `RULESET`, not case-tuned.

The full production C3 corpus and its signed numbers are **deferred evidence**; the local fixture
corpus is a representative stand-in sufficient to demonstrate the FPR delta.

## 5. Scope note

The GW02 parity-harness and expected-diff machinery that would formally register this divergence as a
tracked diff is **deferred**. Until it lands, **this ledger note is the standing record** of the
behaviour change that the GW09 card requires.
