import sys, json, base64, gzip, urllib.parse
sys.path.insert(0, ".skill-workspace/red-team/context-mcp-rt-matrix/agent3/harness")
from lib import call_tool, save_evidence, scan_pii, CANARY

SSN = "123-45-6789"
AWS = "AKIAIOSFODNN7EXAMPLE"
EMAIL = "redteam3.output@corp.example"

findings = []
summary = []

def run(server, tool, args, note, fname):
    rec = call_tool(server, tool, args, note=note, timeout=45)
    body = rec["response"].get("body", {})
    path = save_evidence(rec, fname)
    print(f"[executed] {tool} :: {note} -> {path}")
    return rec, body

# Control: default benign fetch (README.md) to confirm tool + egress works
rec, body = run("everything-mcp", "gzip-file-as-resource",
    {"name": "control.gz", "outputType": "resource"},
    "control: default README fetch (no args = default URL)", "gzip_bypass/control_default.json")
print("  control status:", "isError" in json.dumps(body))

# Attacker-controlled remote content via httpbin echo (URL itself carries the PII as query params -
# simulates "upstream/attacker-controlled content" being fetched and returned as an opaque blob)
url = "https://httpbin.org/get?" + urllib.parse.urlencode({"ssn": SSN, "aws_key": AWS, "email": EMAIL, "canary": CANARY})
rec, body = run("everything-mcp", "gzip-file-as-resource",
    {"name": "leak2.gz", "data": url, "outputType": "resource"},
    "attacker-controlled remote content (httpbin echo of PII/secret in query) fetched+gzipped", "gzip_bypass/httpbin_pii_echo.json")

content = body.get("result", {}).get("content", [])
is_error = body.get("result", {}).get("isError", False)
print("  isError:", is_error)
blocked = any("[BLOCKED]" in (c.get("text","") if isinstance(c,dict) else "") for c in content) if isinstance(content, list) else False
print("  blocked-tag-present:", blocked)

blob_b64 = None
for c in content:
    if isinstance(c, dict) and c.get("type") == "resource":
        blob_b64 = c.get("resource", {}).get("blob")
    if isinstance(c, dict) and "text" in c:
        print("  text content:", c["text"][:300])

result_entry = {"note": "httpbin_pii_echo", "isError": is_error, "blocked_tag": blocked, "has_blob": bool(blob_b64)}

if blob_b64:
    raw_gz = base64.b64decode(blob_b64)
    try:
        decompressed = gzip.decompress(raw_gz)
        text = decompressed.decode("utf-8", errors="replace")
        print("  DECOMPRESSED CONTENT (first 800 chars):")
        print(" ", text[:800])
        raw_hits = {}
        for label, val in [("SSN", SSN), ("AWS_KEY", AWS), ("EMAIL", EMAIL), ("CANARY", CANARY)]:
            if val in text:
                raw_hits[label] = True
        print("  RAW PII/SECRET FOUND IN DECOMPRESSED BLOB:", raw_hits)
        result_entry["decompressed_preview"] = text[:2000]
        result_entry["raw_hits_in_decompressed_blob"] = raw_hits
        # Also check: does the base64 blob ITSELF (without decompression) contain any raw pattern (it shouldn't, it's binary)
        blob_text_check = blob_b64
        raw_hits_blob_b64 = {k: (v in blob_text_check) for k,v in [("SSN", SSN), ("AWS_KEY", AWS), ("EMAIL", EMAIL)] if v in blob_text_check}
        result_entry["raw_hits_in_base64_blob_itself"] = raw_hits_blob_b64
        if raw_hits:
            findings.append({"finding": "OUTPUT DLP BYPASS via gzip-compressed opaque blob",
                              "server": "everything-mcp", "tool": "gzip-file-as-resource",
                              "detail": "Attacker-controlled remote URL content (containing raw SSN/AWS-key/email) fetched and returned as a gzip+base64 'resource' blob without any redaction; client-side gunzip recovers all raw PII/secrets verbatim.",
                              "raw_hits": raw_hits, "evidence": "gzip_bypass/httpbin_pii_echo.json"})
    except Exception as e:
        print("  decompress failed:", e)
        result_entry["decompress_error"] = str(e)

summary.append(result_entry)

with open(".skill-workspace/red-team/context-mcp-rt-matrix/agent3/evidence/_run4_gzip_bypass_summary.json", "w") as f:
    json.dump(summary, f, indent=2, default=str)
with open(".skill-workspace/red-team/context-mcp-rt-matrix/agent3/evidence/_run4_gzip_bypass_findings.json", "w") as f:
    json.dump(findings, f, indent=2, default=str)

print(f"\n=== FINDINGS: {len(findings)} ===")
