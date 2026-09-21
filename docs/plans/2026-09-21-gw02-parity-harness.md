# GW02 Implementation Plan — parity harness

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make “v2 behaves correctly” a measured claim: C2 recorded shapes (≥50k), a four-bucket replay differ, an expected-diff ledger that rejects post-hoc timestamps, and a C3 scorecard of v1 against the labelled detection corpus.

**Architecture:** Harness lives in `gateway_v2.contracts.parity` (lowest layer). C2 is generated from surface×mode×tenant templates and sanitized at capture. Replay uses `FrozenClock` so ids/`created` are deterministic. Differ buckets: IDENTICAL / EXPECTED / UNEXPECTED / WIRE. Ledger entries cannot excuse WIRE. C3 loads `tests/detection_corpus/` (T04 retained). v1 Tier-1 oracle imports `ATTACK_PATTERNS` (no v1 module edits). Reuse T02 recorder *shapes*; do not rebuild staging nginx.

**Tech Stack:** Python 3.12, pytest, existing G0.1 JSONL corpus, GitHub Actions job `parity-harness`.

**Locks:** no v1 application-module edits; no Docker wipe; live stack stays; LGW00-1 still residual; C2 under-coverage reports a gap and blocks GW21 not GW02.

---

### Task 1: Types, clock, sanitizer, C2 generator

50k records, every surface and both streaming modes, opposite-policy tenants, sanitize at write.

### Task 2: Differ + ledger timestamp gate

WIRE first. Ledger only for disposition/transform diffs. Git commit time wins over a back-dated field.

### Task 3: Replay + C3 v1 scorecard

v1 self-replay IDENTICAL. Canonical five backtick prompts 5/5 block. Paraphrase family recall 0.

### Task 4: CI + evidence
