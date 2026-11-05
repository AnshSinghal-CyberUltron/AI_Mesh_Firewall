"""Live request-id-uniqueness driver (task 5). Every call is a REAL HTTP request (stdlib only).

BEFORE = amfint (old content-hash ids, :38301). AFTER = amff2 (this worktree's fix, :63301).
All requests, statuses, selected headers and body excerpts are appended to JSONL files under
this evidence directory. Nothing here is product code.
"""
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

EVID = "docs/plans/evidence/2026-11-05-request-id-uniqueness"
KEY = "dev-key-change-me"
ID_RE = re.compile(r"^zs-dev-org-[0-9a-f]{24}$")

STACKS = {
    "amfint": {"g": "http://127.0.0.1:38301", "redis": "amfint-redis-1"},
    "amff2": {"g": "http://127.0.0.1:63301", "redis": "amff2-redis-1"},
}

_HDR_KEEP = ("x-request-id", "x-zeroshield-action", "content-type", "retry-after", "www-authenticate")


class Rec:
    def __init__(self, name):
        self.path = f"{EVID}/{name}.jsonl"
        open(self.path, "w").close()

    def call(self, label, method, url, body=None, key=KEY, excerpt=900, extra_headers=None):
        h = {"content-type": "application/json"}
        if key:
            h["authorization"] = f"Bearer {key}"
        if extra_headers:
            h.update(extra_headers)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, headers=h, method=method)
        t = time.time()
        try:
            r = urllib.request.urlopen(req, timeout=60)
            status = r.status
            resp_headers = {k.lower(): v for k, v in r.headers.items()}
            body_text = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            status = e.code
            resp_headers = {k.lower(): v for k, v in e.headers.items()}
            body_text = e.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            status = -1
            resp_headers = {}
            body_text = f"<exception {type(e).__name__}: {e}>"
        rec = {
            "label": label,
            "request": {"method": method, "url": url, "body": body},
            "status": status,
            "resp_headers": {k: v for k, v in resp_headers.items() if k in _HDR_KEEP},
            "body_excerpt": body_text[:excerpt],
            "ms": round((time.time() - t) * 1000),
        }
        open(self.path, "a").write(json.dumps(rec) + "\n")
        return rec


def body_request_id(rec):
    txt = rec["body_excerpt"]
    for m in re.finditer(r'"request_id"\s*:\s*"(zs-[^"]+)"', txt):
        return m.group(1)
    m = re.search(r'chatcmpl-(zs-[0-9a-f-]+)', txt)
    if m:
        return m.group(1)
    return None


def header_request_id(rec):
    return rec["resp_headers"].get("x-request-id")


def redis_audit_ids(container, org="dev-org"):
    out = subprocess.run(
        ["docker", "exec", container, "redis-cli", "XRANGE", f"{{rv2}}:audit:{org}", "-", "+"],
        capture_output=True, text=True).stdout
    return re.findall(r'"request_id":"(zs-[^"]+)"', out)


def redis_audit_flush(container, org="dev-org"):
    subprocess.run(["docker", "exec", container, "redis-cli", "DEL", f"{{rv2}}:audit:{org}"],
                   capture_output=True, text=True)


def chat_body(text, stream=False):
    return {"model": "echo-1", "messages": [{"role": "user", "content": text}], "stream": stream}


def b_chat_nonstream(rec, g, label):
    return rec.call(label, "POST", f"{g}/v1/chat/completions", chat_body("identical chat probe alpha"))


def b_chat_stream(rec, g, label):
    # urllib returns the fully-buffered SSE body; the start header x-request-id is captured too.
    return rec.call(label, "POST", f"{g}/v1/chat/completions", chat_body("identical stream probe beta", stream=True))


def b_mcp(rec, g, label):
    return rec.call(label, "POST", f"{g}/v1/mcp/dev-mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})


def b_embeddings(rec, g, label):
    return rec.call(label, "POST", f"{g}/v1/embeddings", {"model": "echo-1", "input": "identical embed probe"})


def b_rag(rec, g, label):
    return rec.call(label, "POST", f"{g}/v1/rag/query", {"query": "identical rag probe", "collection": "dev"})


SURFACES = [
    ("chat_nonstream", b_chat_nonstream, body_request_id),
    ("chat_stream", b_chat_stream, lambda r: header_request_id(r) or body_request_id(r)),
    ("mcp_tools_list", b_mcp, body_request_id),
    ("embeddings", b_embeddings, body_request_id),
    ("rag_query", b_rag, body_request_id),
]


def run_five(rec, g, prefix, builder, extract, concurrent):
    labels = [f"{prefix} #{i+1}" for i in range(5)]
    if concurrent:
        with ThreadPoolExecutor(max_workers=5) as ex:
            recs = list(ex.map(lambda lab: builder(rec, g, lab), labels))
    else:
        recs = [builder(rec, g, lab) for lab in labels]
    return [extract(r) for r in recs]


def phase(name):
    stack = STACKS[name]
    g = stack["g"]
    rec = Rec(f"{name}-five-identical")
    result = {}
    for sid, builder, extract in SURFACES:
        seq = run_five(rec, g, f"[{name}] {sid} seq", builder, extract, False)
        con = run_five(rec, g, f"[{name}] {sid} concurrent", builder, extract, True)
        result[sid] = {"sequential_ids": seq, "concurrent_ids": con}
    return result


if __name__ == "__main__":
    which = sys.argv[1]
    print(json.dumps(phase(which), indent=2))
