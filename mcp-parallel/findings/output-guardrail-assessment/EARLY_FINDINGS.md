# Early findings — Output Guardrail Assessment

Evidence as of 2026-07-10. Report-first; fixes deferred to Phase 6 by severity.

## Confirmed (live +/or unit)

| ID | Severity | Verdict | Evidence |
|----|----------|---------|----------|
| **F-015 / MCP-OG-001** | **CRITICAL** | Zero MCPScanControl → `scan_skipped` → raw RESULT leak | `attack-matrix-mcp-batch1.json` |
| **F-001** | HIGH | UI IP `flag` coerced to `redact` | `proof-f001-ip-flag.json` |
| **F-002** | HIGH | UI `rewrite` + stream → `block` (not rewrite) | `proof-f002-rewrite-stream-v2.json` |
| **F-003** | HIGH | UI `flag` + `enforcement_mode=block` → `block` | `proof-f003-flag-escalation-v2.json` |
| **F-008** | HIGH | §1.7 does not drive MCP/RAG egress | `attack-matrix-mcp-batch1.json` |
| **F-013** | MED | Stream block HTTP 200+SSE vs non-stream 400 | `attack-matrix-chat-batch1.json` |
| **F-009** | LOW | `config_sync_loaded=false` misleading; sync works | `proof-f009-config-sync.json` |

## Open / needs more proof

| ID | Issue |
|----|--------|
| F-004 | Maskable block→redact (by design; UI honesty gap) |
| F-005 | Hallucination toggle live OFF proof |
| F-006 | Output policy Path-D vs stream |
| F-007 | `output_exfil_*` hidden default (not in §1.7 UI) |
| F-010 | Stream does not call `enforce_output` (asymmetry) |
| F-011 | Stream tier-2 degraded may ship |
| F-014 | Shared-stack concurrent config races |

## Chat detector batch1 (when OG reached)

PII email/CC/ZW-SSN/b64 → redact; sk-ant → block; many SSN/AKIA/ghp short-circuited at input_scan. Benign FP clean. See `attack-matrix-chat-batch1.json`.

## Baseline restored

`output_pii_action=redact`, credential=`block`, IP=`flag`, policy=`block`, hallucination off.
