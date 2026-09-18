# GW00 Implementation Plan — gateway_v2 workspace and gates

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Do not start until the user confirms execution. Do not print, commit, or authenticate with key material.

**Goal:** On `revamp` pinned at `877c27a8` (plus the SOT-docs commit), publish the baseline SHA, stand up empty `gateway_v2/` with machine-enforced layer/size/AST gates, and wire a CI workflow that runs those gates on every PR. Credential classification/revocation is **human-only**.

**Architecture:** v2 is a second package beside frozen `gateway/ai_mesh_gateway`. Layers are `edge > admit > plan > detect > resolve > dispatch > egress > audit > runtime > contracts`. Detectors emit findings only; `resolve/` is the only authority. No function >120 lines, no module >800 lines.

**Tech Stack:** Python 3.12, import-linter, ruff, mypy --strict, pytest, GitHub Actions.

**SOT:** `docs/AI_MESH_MASTER_RUNBOOK_v2_BACKEND_REWRITE.docx` wins. `docs/SECTION_10_backend_rewrite_source.md` is the extract.

**Locks (2026-09-18):**
- Branch: `revamp`. Baseline: `877c27a8`.
- Leave T00–T02 lab work in place; T-track is absorbed per §10.12. Next card is GW00, not T03.
- Do not edit v1 application modules. Do not wipe the live Docker stack. GW00 deploys nothing.
- UI00–UI13 may proceed later in parallel; UI06/UI07 wait on GW05+GW14. Not in this card.
- If underspecified: ask, then code. Do not guess.
- User also selected “full gateway pytest in CI”. **Docx GW00 CI is the v2 suite + structural gates; the OpenAI conformance suite is GW01.** Do not turn on the 496-file v1 corpus in this card.

**Human gate (do not skip, do not automate):** LGW00-1 — classify `ai-mesh-firewall` (tracked extensionless OpenSSH private key, 399 bytes, present on main/ansh/revamp). Agents must not print it, must not `ssh` with it, must not paste it. Deleting the file is not revocation. History rewrite is a separate ticket.

**LGW00-6** needs two hosts for identical image digests. A second host was previously waived for L00-2. **Ask before claiming LGW00-6 PASS.** This card can still land gates + a reproducible Dockerfile and record a single-host digest as incomplete.

---

### Task 1: Publish the baseline SHA

**Files:**
- Create: `docs/plans/evidence/2026-09-18-gw00/baseline.json`
- Modify: none of v1 gateway code

**Step 1:** Record `git rev-parse HEAD`, `origin/revamp`, `origin/ansh`, `origin/main` SHAs, and live container image IDs if the stack is up (`docker inspect` only). Do not restart containers.

**Step 2:** Commit `baseline.json` citing `877c27a8` as branch truth (user lock). Every later GW card cites this SHA.

---

### Task 2: Ignore + scan extensionless private keys (no key body)

**Files:**
- Modify: `.gitignore` — add patterns that match extensionless OpenSSH/PEM *filenames we choose*, plus keep `ai-mesh-firewall` from being *re-added* if removed later. Do **not** `git rm --cached` the existing key until the human signs LGW00-1. Adding gitignore does not untrack an already-tracked file.
- Modify: `scripts/ralph/precommit-secret-scan.sh` — grep staged blobs for `BEGIN OPENSSH PRIVATE KEY` and `BEGIN .* PRIVATE KEY` (header strings only).
- Create: `.github/workflows/gateway-v2.yml` job `secret-headers` that `git grep -a` those headers on tracked files and fails if any remain **after** the human removal. Until LGW00-1, this job should **report** the existing tracked path without printing the body, and fail closed only on *new* paths — **ask** if the workflow should fail the PR while the known file is still tracked.

**Negative fixture for LGW00-2:** a CI job that checks out a throwaway file with an OpenSSH header and no extension; the pre-commit/CI must reject it. Do not commit that fixture to `revamp` mainline; generate it in the workflow.

---

### Task 3: Empty `gateway_v2/` tree

**Files (create, empty modules + `__init__.py` only):**

```
gateway_v2/
  pyproject.toml
  gateway_v2/edge/{app.py,openai_chat.py,openai_responses.py,openai_misc.py,mcp.py,rag.py,errors.py,wire/__init__.py}
  gateway_v2/admit/{identity.py,quota.py,killswitch.py,grant.py}
  gateway_v2/plan/{model.py,compiler.py,store.py,snapshot.py}
  gateway_v2/detect/{base.py,windowing.py,deterministic/__init__.py,semantic/__init__.py,guard/{backend.py,local_onnx.py,local_trt.py,triton_grpc.py,remote_http.py}}
  gateway_v2/resolve/{resolver.py,decision.py,conflict.py}
  gateway_v2/dispatch/{provider.py,routing.py,transform.py}
  gateway_v2/egress/{stream.py,backpressure.py,output_guard.py,strict.py}
  gateway_v2/audit/{record.py,sink.py,metrics.py}
  gateway_v2/runtime/{resources.py,lifecycle.py,clock.py}
  gateway_v2/contracts/openai_conformance/__init__.py
  tests/ (placeholder test that imports the package)
```

Each `.py` is a module docstring + `pass` / empty `__all__`. No function may exceed 120 lines (they will be far under). No HTTPException anywhere yet.

**`pyproject.toml` must declare import-linter layers** in this order:
`edge > admit > plan > detect > resolve > dispatch > egress > audit > runtime > contracts`
A module may import strictly lower layers and `runtime`/`contracts`. Never upward or sideways.

---

### Task 4: Five structural gates (must fail on bad input, pass on empty tree)

**Files:**
- Create: `gateway_v2/lint/check_sizes.py` — fail if any function >120 lines or module >800.
- Create: `gateway_v2/lint/check_http_outside_edge_resolve.py` — AST: `HTTPException`, `JSONResponse`, `status_code=4xx` only allowed under `edge/` and `resolve/`.
- Create: `gateway_v2/lint/check_no_module_mutable.py` — AST: reject module-level `list`/`dict`/`set` literals assigned at module scope (allow `TYPE_CHECKING` and annotations).
- `import-linter` via `[tool.importlinter]` in `gateway_v2/pyproject.toml`.
- `ruff` + `mypy --strict` on `gateway_v2/`.

**Negative fixtures (CI-only, not merged into library code):**
- LGW00-3: `from gateway_v2.edge import app` inside `detect/base.py` → import-linter fails naming the contract.
- LGW00-4: 130-line function + 850-line module → size lint fails with file and line.
- LGW00-5: `raise HTTPException(403)` in `detect/` → AST gate names the file.

Implement fixtures as workflow steps that copy a bad snippet into a temp tree and run the same checkers, **or** as tests under `gateway_v2/tests/gates/` that invoke the checkers on in-memory sources. Do not leave the violations in the shipped tree.

---

### Task 5: CI workflow

**Files:**
- Create: `.github/workflows/gateway-v2.yml`
- Do **not** replace `chat-pipeline-golden.yml`.

On every PR / push to `revamp`:
1. size + HTTP AST + mutable-state gates
2. import-linter
3. ruff + mypy --strict
4. `pytest` of the v2 placeholder suite
5. negative-fixture jobs for LGW00-2..5
6. OpenSSH/PEM header scan (see Task 2)

GW01 will add `openai` install + conformance suite. Do not install `openai` in GW00 unless needed for an empty skip.

---

### Task 6: Reproducible image (LGW00-6 partial)

**Files:**
- Create: `gateway_v2/Dockerfile` FROM python:3.12-slim, copy `gateway_v2/` + `shared/`, pin digest later.
- Evidence: build once on this VM, record digest in `docs/plans/evidence/2026-09-18-gw00/image-digest.json`.
- Second-host rebuild is **blocked** until the user unlocks a second host or explicitly waives LGW00-6 the same way as L00-2.

---

### Task 7: Live / local verification (this VM)

Not a product deploy. Required commands:

```bash
cd gateway_v2 && python -m pytest -q
# import-linter, ruff, mypy, size/AST gates: all green on empty tree
# negative fixtures: all fail as specified
```

Do **not** recreate Docker frontend/gateway for GW00. Leave Vite/:8180 and v1 gateway running if they are up.

---

### Exit criteria (docx)

- [ ] Credential classified; if genuine, revoked/rotated with a **signed human record** (LGW00-1). Agent cannot close this.
- [ ] Baseline SHA published (`revamp` / `877c27a8` + SOT commit).
- [ ] `gateway_v2/` exists, empty, five gates fail on bad input and pass on the empty tree.
- [ ] CI workflow runs on every PR.
- [ ] LGW00-6: two identical digests on different hosts **or** an explicit user waiver.

### Stop rule

No rollback (nothing deployed). **Stop** if the key cannot be classified within one working day — escalate rather than issue new staging credentials.

---

Plan complete. Two execution options after you confirm:

**1. Subagent-Driven (this session)** — one subagent per task above, review between tasks. Skip Task 1 human auth; leave LGW00-1 open.

**2. Parallel Session** — new session with executing-plans.

**Which approach?** Also answer: (a) fail CI while `ai-mesh-firewall` remains tracked, or only fail *new* extensionless keys? (b) waive LGW00-6 second host?
