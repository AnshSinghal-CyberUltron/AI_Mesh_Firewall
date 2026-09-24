"""Scan the evidence bundle (including inside .zst files) for real credentials before it is committed.
Never prints a secret: reports file, pattern name and a redacted 6-char prefix only.
Exit 1 if any REAL-credential pattern is found (private keys, the controller's HF token, GCP SA keys, live-looking provider keys)."""
import hashlib
import os
import re
import subprocess
import sys

ROOT = sys.argv[1]
HF_TOKEN_FILE = os.path.expanduser("~/.cache/huggingface/token")
hf = open(HF_TOKEN_FILE).read().strip() if os.path.exists(HF_TOKEN_FILE) else None
REPO_KEY = "/home/contact_cyberultron_com/AI_Mesh_Firewall/ai-mesh-firewall"
repo_key_lines = []
if os.path.exists(REPO_KEY):
    repo_key_lines = [l.strip() for l in open(REPO_KEY, errors="ignore") if len(l.strip()) > 40 and "PRIVATE KEY" not in l]
SP_KEY = "/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/gcp/rv_ed25519"
sp_key_lines = [l.strip() for l in open(SP_KEY, errors="ignore") if len(l.strip()) > 40 and "PRIVATE KEY" not in l] if os.path.exists(SP_KEY) else []

REAL = {
    "private_key_block": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----"),
    "gcp_sa_private_key": re.compile(rb'"private_key_id"\s*:\s*"[0-9a-f]{40}"'),
    "hf_token_shape": re.compile(rb"hf_[A-Za-z0-9]{34,}"),
    "openrouter_key": re.compile(rb"sk-or-v1-[a-f0-9]{64}"),
    "anthropic_key": re.compile(rb"sk-ant-api\d{2}-[A-Za-z0-9_\-]{80,}"),
    "openai_key": re.compile(rb"sk-(?:proj-)?[A-Za-z0-9]{40,}"),
    "google_api_key": re.compile(rb"AIza[0-9A-Za-z_\-]{35}"),
}
CANARY = {  # synthetic test data the benches inject on purpose; reported, not failing
    "aws_access_key_id": re.compile(rb"AKIA[0-9A-Z]{16}"),
    "github_pat": re.compile(rb"gh[pousr]_[A-Za-z0-9]{36}"),
}


def content(path):
    if path.endswith(".zst"):
        return subprocess.run(["zstdcat", path], capture_output=True).stdout
    with open(path, "rb") as f:
        return f.read()


def main():
    real_hits, canary_hits, n = [], {}, 0
    exact = [x.encode() for x in ([hf] if hf else []) + repo_key_lines + sp_key_lines]
    for root, _d, files in os.walk(ROOT):
        for fn in files:
            p = os.path.join(root, fn)
            n += 1
            try:
                b = content(p)
            except Exception as e:  # noqa: BLE001
                print("UNREADABLE", p, e)
                continue
            for x in exact:
                if x and x in b:
                    real_hits.append((p, "EXACT controller secret (HF token or SSH key material)", hashlib.sha256(x).hexdigest()[:8]))
            for name, rx in REAL.items():
                for m in rx.finditer(b):
                    real_hits.append((p, name, m.group(0)[:6].decode(errors="replace") + "…"))
                    break
            for name, rx in CANARY.items():
                if rx.search(b):
                    canary_hits.setdefault(name, []).append(p)
    print(f"scanned {n} files under {ROOT}")
    for name, ps in canary_hits.items():
        print(f"synthetic-canary pattern {name}: {len(ps)} files (e.g. {os.path.relpath(ps[0], ROOT)})")
    for p, name, pre in real_hits:
        print(f"REAL-CREDENTIAL PATTERN {name} in {os.path.relpath(p, ROOT)} ({pre})")
    sys.exit(1 if real_hits else 0)


if __name__ == "__main__":
    main()
