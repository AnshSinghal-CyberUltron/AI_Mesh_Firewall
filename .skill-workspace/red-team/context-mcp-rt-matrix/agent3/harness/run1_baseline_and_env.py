import sys, json, base64
sys.path.insert(0, ".skill-workspace/red-team/context-mcp-rt-matrix/agent3/harness")
from lib import call_tool, save_evidence, scan_pii, CANARY

SERVERS = ["everything-mcp", "everything-1", "cp09-ens8do"]

findings = []

def run(server, tool, args, note, fname):
    rec = call_tool(server, tool, args, note=note)
    pii = scan_pii(rec["response"])
    rec["_pii_scan"] = pii
    path = save_evidence(rec, fname)
    status = "PII-FOUND-IN-OUTPUT" if pii else "clean"
    print(f"[{status}] {server}/{tool} :: {note} -> {path}")
    if pii:
        findings.append({"server": server, "tool": tool, "note": note, "pii": pii, "evidence": path})
    return rec

# 1) get-env baseline — does it dump secrets?
for s in ["everything-mcp"]:
    run(s, "get-env", {}, "baseline env dump - check for secrets/keys", f"{s}/get_env_baseline.json")

# 2) get-tiny-image / get-annotated-message / get-resource-links / get-resource-reference baseline
run("everything-mcp", "get-tiny-image", {}, "baseline", "everything-mcp/get_tiny_image.json")
run("everything-mcp", "get-annotated-message", {"messageType": "debug", "includeImage": False}, "baseline debug annotate", "everything-mcp/get_annotated_debug.json")
run("everything-mcp", "get-annotated-message", {"messageType": "error", "includeImage": False}, "baseline error annotate", "everything-mcp/get_annotated_error.json")
run("everything-mcp", "get-resource-links", {"count": 10}, "baseline resource links x10", "everything-mcp/get_resource_links.json")
for rid in [1,2,3,4,5]:
    run("everything-mcp", "get-resource-reference", {"resourceType": "Text", "resourceId": rid}, f"resource id {rid}", f"everything-mcp/get_resource_ref_{rid}.json")
run("everything-mcp", "get-structured-content", {"location": "New York"}, "baseline structured content", "everything-mcp/get_structured_content.json")

# 3) benign echo with canary
run("everything-mcp", "echo", {"message": f"benign {CANARY}"}, "benign echo", "everything-mcp/echo_benign.json")

# 4) get-sum with weird numeric edge/type-confusion (string injection attempt via number field is blocked by schema; try huge numbers)
run("everything-mcp", "get-sum", {"a": 1, "b": 2}, "baseline sum", "everything-mcp/get_sum_baseline.json")

with open(".skill-workspace/red-team/context-mcp-rt-matrix/agent3/evidence/_run1_findings.json", "w") as f:
    json.dump(findings, f, indent=2)
print("\nTOTAL FINDINGS:", len(findings))
