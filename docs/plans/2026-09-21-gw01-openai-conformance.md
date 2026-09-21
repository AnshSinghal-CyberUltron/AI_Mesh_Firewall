# GW01 Implementation Plan — OpenAI wire-contract freeze

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the frozen OpenAI SDK contract (`base_url` + API key only) an executable suite that runs against v1 on every `revamp` commit, plus Node, real TCP, a malformed-frame negative, and MCP/RAG/vector wire shapes.

**Architecture:** Lift the six v1 files into `gateway_v2/tests/openai_conformance/` (outside the 800-line package lint). Thin env resolver lives in `gateway_v2.contracts.openai_conformance`. Do not edit v1 application modules. Default pytest still ignores this suite so GW00 structural-gates stay small.

**Tech Stack:** Python 3.12, `openai==2.38.0`, pytest-asyncio, fakeredis, Node `openai` SDK, GitHub Actions.

**Locks (2026-09-21):** waive LGW00-1 residual; keep local 0600 identity; RevokedKeys already absent; full LGW01-1..5; env-selected app; live TCP `:8300` (nginx-absent ledger); gateway recreate allowed; D4/D5 strict must-pass; MCP/RAG/vector shapes now; Node in GHA and live; commit+push `revamp`.

**Honesty split:** the 77 Python cells assert stubbed upstream bytes (`Hello from upstream.`). They run in-process ASGI and on loopback uvicorn. They are **not** the live `:8300` corpus. Live `:8300` is a wire probe (auth error, JSON envelopes, SSE content-type) because this host has no staging nginx and a real LLM would break stub assertions.

---

### Task 1: Harness + copied suite, D4/D5 strict

Copy the six files. Point sibling imports at the copy. Remove `@pytest.mark.xfail(strict=False)` from D4 and D5 only.

### Task 2: Extra surfaces + malformed frame + live TCP probe

New tests under the same directory. Malformed `data:` frame must make the SDK raise and name the frame.

### Task 3: Node matrix

Pinned npm `openai`. Streaming + tools + typed auth error. No custom parser.

### Task 4: CI + run-twice

New `openai-conformance` job: Python ASGI, Python loopback TCP, Node vs loopback, malformed-frame gate, junit compare twice.

### Task 5: Evidence + push
