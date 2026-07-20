import sys, json, base64, gzip
sys.path.insert(0, ".skill-workspace/red-team/context-mcp-rt-matrix/agent3/harness")
from lib import call_tool, save_evidence, CANARY

SSN = "123-45-6789"
AWS = "AKIAIOSFODNN7EXAMPLE"
EMAIL = "redteam3.output@corp.example"
GIST_CANARY = "RT3GZIPBYPASS7788"

RAW_URL = "https://gist.githubusercontent.com/AnshSinghal/c3e11fbf92cc0f8784f3c4f169efa48a/raw/rt3_payload.json"

rec = call_tool("everything-mcp", "gzip-file-as-resource",
    {"name": "remote_pii.gz", "data": RAW_URL, "outputType": "resource"},
    note="remote gist URL (no PII in URL string) whose CONTENT has raw SSN/AWS-key/email/canary - fetched+gzipped by the tool",
    timeout=45)

body = rec["response"].get("body", {})
content = body.get("result", {}).get("content", [])
is_error = body.get("result", {}).get("isError", False)
blocked = any("[BLOCKED]" in (c.get("text","") if isinstance(c,dict) else "") for c in content) if isinstance(content, list) else False
print("HTTP:", rec["response"].get("http_status"), "isError:", is_error, "blocked_tag:", blocked)

blob_b64 = None
for c in content:
    if isinstance(c, dict):
        if c.get("type") == "resource":
            blob_b64 = c.get("resource", {}).get("blob")
            print("resource uri:", c.get("resource",{}).get("uri"), "mimeType:", c.get("resource",{}).get("mimeType"))
        if "text" in c:
            print("text content:", c["text"][:400])

result = {"blocked_tag": blocked, "is_error": is_error, "has_blob": bool(blob_b64)}

if blob_b64:
    raw_gz = base64.b64decode(blob_b64)
    print("gzip blob raw byte length:", len(raw_gz))
    # Check: does the base64 STRING itself (as it appears in the JSON response, pre-decompression)
    # contain the raw PII substrings anywhere (sanity: should not, since it's compressed binary)
    for label, val in [("SSN", SSN), ("AWS_KEY", AWS), ("EMAIL", EMAIL), ("GIST_CANARY", GIST_CANARY)]:
        print(f"  raw substring '{label}' present in base64 blob text (pre-decompress)?", val in blob_b64)
    try:
        decompressed = gzip.decompress(raw_gz)
        text = decompressed.decode("utf-8", errors="replace")
        print("\nDECOMPRESSED CONTENT:")
        print(" ", text)
        raw_hits = {}
        for label, val in [("SSN", SSN), ("AWS_KEY", AWS), ("EMAIL", EMAIL), ("GIST_CANARY", GIST_CANARY)]:
            raw_hits[label] = (val in text)
        print("\nRAW PII/SECRET RECOVERED AFTER CLIENT-SIDE GUNZIP:", raw_hits)
        result["decompressed_content"] = text
        result["raw_hits_after_gunzip"] = raw_hits
    except Exception as e:
        print("decompress error:", e)
        result["decompress_error"] = str(e)

rec["_analysis"] = result
path = save_evidence(rec, "gzip_bypass/remote_gist_pii_via_gzip_blob.json")
print("\nEvidence saved:", path)

with open(".skill-workspace/red-team/context-mcp-rt-matrix/agent3/evidence/_run5_result.json", "w") as f:
    json.dump(result, f, indent=2, default=str)
