"""mitmproxy addon — capture the gateway's ACTUAL outbound egress to the real
fleet (OpenRouter / Bedrock / embedding / Pinecone) and scan the WIRE BYTES for
raw PII/secrets. This is the response-independent egress truth the leak-hunt
needs: pipeline_trace.prompt_out is a pre-_apply_redaction trace, the client
response is the model output — only the bytes on the wire prove the forwarded
prompt is masked.

Run:  mitmdump -s tests/leakhunt/capture_addon.py -p 8081
Point the gateway's outbound HTTPS through it (HTTPS_PROXY=http://host.docker.internal:8081
+ trust ~/.mitmproxy CA via SSL_CERT_FILE/REQUESTS_CA_BUNDLE/AWS_CA_BUNDLE).
Captures land in /tmp/leakhunt_wire.jsonl  (Authorization headers are redacted).
"""
import json
import re
import time

from mitmproxy import http

_PII = {
    "ssn": re.compile(r"\b9?\d{2}-\d{2}-\d{4}\b"),
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    "phone10": re.compile(r"(?<!\d)\d{10}(?!\d)"),
    "card": re.compile(r"\b(?:\d[ -]?){13,16}\b"),
    "openai_key": re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    "aws_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}\b"),
    "github": re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
}
# Provider hosts the gateway egresses to (NOT internal control/redis/pg/chroma).
_TARGETS = (
    "openrouter.ai", "api.openai.com", "anthropic.com",
    "bedrock", "amazonaws.com", "pinecone.io",
)
_LOG = "/tmp/leakhunt_wire.jsonl"


def _scan(body: bytes):
    try:
        text = body.decode("utf-8", "replace")
    except Exception:
        return []
    return sorted(k for k, rx in _PII.items() if rx.search(text))


def request(flow: http.HTTPFlow) -> None:
    host = flow.request.pretty_host
    if not any(t in host for t in _TARGETS):
        return
    body = flow.request.content or b""
    # The Authorization header carries the BYOK key — do NOT scan/log it as a leak.
    rec = {
        "ts": time.time(),
        "host": host,
        "path": flow.request.path.split("?")[0],
        "method": flow.request.method,
        "body_len": len(body),
        "pii_on_wire": _scan(body),  # the actual leaked PII in the EGRESS prompt, if any
        "preview": body[:1800].decode("utf-8", "replace"),
    }
    with open(_LOG, "a") as fh:
        fh.write(json.dumps(rec) + "\n")
