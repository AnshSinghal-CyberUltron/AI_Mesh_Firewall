# File Validation Report

## Objective

Prove the customer integration pattern:

**Upload → local text extraction → gateway-governed `responses.create`**

The demo server parses PDF/DOCX/TXT/CSV locally. Only extracted text is sent to ZeroShield inside a normal inference request. Raw binaries are never sent to the gateway or model provider.

## In scope / out of scope

| In scope | Out of scope |
|----------|--------------|
| PDF, DOCX, TXT, CSV upload in demo UI | OpenAI `/v1/files` API (gateway returns 404 by design) |
| Local extraction (`pypdf`, `python-docx`, CSV parser) | Vector indexing (use **RAG** tab) |
| Multi-file upload with partial-parse warnings | MCP context on file path |
| Gateway analysis via `responses.create` | SDK Scenarios button (multipart required) |
| Customer-safe `files_manifest` (name, status, char count) | Echoing full extracted document text in API responses |

## Customer demo flow

1. **Files** tab → choose PDF/TXT/CSV/DOCX
2. Confirm selected filenames appear in preview (no content echoed)
3. Click **Analyze via Gateway**
4. Show verdict banner + AI summary + documents processed table
5. Open pipeline sidebar for governance proof (input scan, routing, output guard)

## Demo status reasons

| Code | Meaning |
|------|---------|
| `allowed` | Document text passed all gateway stages |
| `file_unreadable` | No files could be parsed locally |
| `file_content_blocked` | Sensitive/policy-violating content in document text blocked |
| `file_content_redacted` | Sensitive document content redacted; analysis continued |
| `redacted_allowed` | Generic redaction path (fallback) |
| `blocked_policy` | Generic block (non-files-specific fallback) |

Partial parse: top-level `status_reason` stays `allowed` with `details` listing skipped files.

## Test vectors

| Vector | Input | Expected |
|--------|-------|----------|
| Benign CSV | `team.csv` with names/roles | `200`, `files_manifest.chars > 0`, `analysis.content`, pipeline visible |
| Sensitive TXT | fake SSN in document body | `file_content_blocked`, `file_content_redacted`, or `redacted_allowed` |
| Unsupported | `.exe` or unknown extension | `400`, `file_unreadable`, no `analysis` |
| Partial multi-file | valid CSV + bad file | `200`, `file_warnings`, top reason not `file_unreadable` |

## Executable gates

```bash
# Demo unit
cd demo/zeroshield-openai-demo
pytest tests/test_status_reason.py tests/test_files_scenario.py -q

# Backend E2E (demo server on :8765)
DEMO_URL=http://127.0.0.1:8765 python tests/validation_backend.py

# UI E2E
cd tests/playwright
DEMO_URL=http://127.0.0.1:8765 npx playwright test demo.spec.mjs -g files
```

## Pass criteria

- Successful analyze returns `files_manifest` without raw `"text"` fields
- `analysis.pipeline` or `analysis.zeroshield` visible on success
- Sensitive document content produces an explicit governed verdict
- Partial parse does not downgrade a successful analysis to `file_unreadable`

## Rollback triggers (block release)

| Symptom | Action |
|---------|--------|
| Full document text echoed in API response | Block release; revert `server.py` manifest contract |
| Partial parse shows `file_unreadable` when analysis succeeded | Block release; fix `attach_files_response` precedence |
| Successful analyze missing pipeline metadata | Block release; verify `scenario_files_analyze` |
| Sensitive document gets `allowed` with no scan evidence | Investigate gateway input scan configuration |

Revert `server.py` response shape and `web/app.js` renderer together to avoid UI/API drift.

## Integration pattern (customer apps)

```python
# 1. Extract text in your app (never send raw PDF bytes to ZeroShield)
text = extract_pdf_text(uploaded_bytes)

# 2. Send extracted text through governed responses.create
response = client.responses.create(
    model="auto",
    input=f"Summarize this document:\n\n{text[:120000]}",
)
summary = response.output_text
```

Do **not** call `client.files.create` against ZeroShield — `/v1/files` is not implemented.
