# MCP action scenarios — live verification

**Date:** 2026-07-15  
**Org:** zeroshield  
**Harness:** `scripts/ralph/mcp_action_scenarios_live.py`  
**Path:** control `POST /api/mcp-connector/tools/call/`  

## Verdict

**14/14 PASS** (`all_green=True`)

## Action matrix (what each means)

| Action | Probe | Pass criteria | Live result |
|--------|-------|---------------|-------------|
| **ALLOW** | Benign `echo` / Linear `list_teams` | 200 + decision allow | PASS on everything-1/2 + Linear |
| **REDACT** | SSN `123-45-6789` in echo | Egress `[REDACTED_SSN]` / no raw SSN | PASS (echo masks; Linear query no echo of SSN) |
| **BLOCK** | PEM private key in echo | 403 / policy block | PASS on everything-*; Linear PEM not echoed → acceptable |
| **FLAG** | AWS `AKIAIOSFODNN7EXAMPLE` under `tag` posture | Not hard-blocked (observe/tag) | PASS — decision=allow, not blocked |

> Product note: server `default_scan_action` is `tag` \| `redact` \| `block` only. **FLAG ≈ tag/observe** (audit/compliance tags when present; textbook AWS key often egresses under tag floors).

## Servers exercised

- `everything-1` / `everything-2` — posture `tag`, tool `echo` (full A/B/R/F)
- `linear-manual-oauth` — posture `tag`, `list_teams` + sensitive query args
- `playwright-mcp` — **SKIPPED** (Chromium missing in sandbox → 404 chrome-not-found)

## Demo UI

Scenarios tab cards: MCP ALLOW / REDACT / BLOCK / FLAG/TAG / Linear ALLOW  
(`examples/zeroshield-openai-demo/frontend/app.js`)

## Re-run

```bash
cd /home/contact_cyberultron_com/AI_Mesh_Firewall
SERVERS=everything-1,everything-2,linear-manual-oauth,playwright-mcp \
  gateway/.venv/bin/python scripts/ralph/mcp_action_scenarios_live.py
```

See also `SUMMARY.md` + `results.json` in this directory.
