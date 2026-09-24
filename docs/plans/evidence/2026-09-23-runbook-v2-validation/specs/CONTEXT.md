# Shared context for runbook-v2 validation agents (read this first)

REPO (working tree, branch revamp @ 52a584e9): /home/contact_cyberultron_com/AI_Mesh_Firewall
BASELINE the runbook audited (ansh @ 2a657fad), clean export (gateway/ shared/ deploy/ services/ scripts/ .github/ control/ workers/ tests/ compose files):
  /tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/baseline-2a657fad
  (2a657fad is an ancestor of revamp; between them only llm_router.py, pipeline_trace.py, entrypoint.sh, docker-compose.prod.yml + tests changed in v1)
RUNBOOK as markdown (converted from the .docx): .../scratchpad/runbook/rb.md
  §1-§8 T-track: lines 1-1372 · §9 frontend: 1373-2017 · §10 backend rewrite: 2019-3373
  §10.1 defects 2025-2109 · §10.2-10.7 design 2110-2292 · §10.8 task index 2293-2323 · cards GW00-GW24 2324-3278 · §10.10-10.13 3279-3373
gateway_v2 (GW00-GW03 done): /home/contact_cyberultron_com/AI_Mesh_Firewall/gateway_v2
Python: /home/contact_cyberultron_com/AI_Mesh_Firewall/gateway/.venv/bin/python (3.14, has gateway deps). uv at ~/.local/bin/uv. gateway_v2 dev tools: check gateway_v2 for a venv or `uv run --with import-linter ...`.
To import BASELINE v1 code: PYTHONPATH=<baseline>/gateway:<baseline>/shared <venv python> ...
Existing perf evidence: docs/perf/evidence/*.md, docs/plans/2026-09-02-hot-path-cost-matrix.md, docs/plans/evidence/*
EVIDENCE OUTPUT DIR (write scripts + raw outputs here, one subfolder per agent): .../scratchpad/evidence/<your-agent-name>/

RULES
- Read-only with respect to the repo: do NOT edit, commit, or push anything in the repo. Do NOT touch the running docker stack. Work in scratchpad.
- Security/secrets are OUT OF SCOPE (user is rotating them). Never print secret values.
- Evidence standard: every verdict must cite a command you ran + its output (saved to your evidence dir) or file:line you read. Execute code where the claim is about behaviour. No guessing, no estimates.
- Verdict vocabulary per claim: CONFIRMED | REFUTED | PARTIAL (say exactly which part) | UNVERIFIABLE (say why) | STALE (true at baseline, changed at HEAD).
- Line-number claims: check the exact cited line at BASELINE; if the content is nearby but offset, say so (PARTIAL: content true, line off by N).
