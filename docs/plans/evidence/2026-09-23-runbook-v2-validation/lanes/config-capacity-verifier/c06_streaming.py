"""§10.1.5 rows 6-8: drive the REAL SecureStreamingResponse with fake upstreams.

A) BLOCK is a truncation: bytes released to the client before the error frame.
B) Output guard runs per flush: inspect() count for 300 token deltas (+ corpora).
C) Rewrite path never clears the buffer on non-DONE flushes: buffer length per
   guard call, number of guard calls and total bytes re-scanned vs stream length.
Production constructor values (main.py:4188-4193): buffer_max_bytes=4096,
max_buffer_chunks=64 (config.py:153-156 env defaults).
"""
import asyncio
import json
import os
import sys

os.environ.setdefault("ENABLE_TIER2", "false")

from secure_streaming import SecureStreamingResponse, STREAM_FLUSH_BYTES  # noqa: E402


def sse(content):
    return "data: " + json.dumps({"id": "c", "object": "chat.completion.chunk",
                                  "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}]}) + "\n\n"


async def upstream(pieces):
    for p in pieces:
        yield sse(p)
    yield "data: [DONE]\n\n"


class V:
    def __init__(self, action, threat_type="none"):
        self.action = action
        self.threat_type = threat_type
        self.detail = f"{threat_type} (test)"
        self.matched_patterns = []
        self.compliance_tags = []
        self.matched_values = {}
        self.scan_degraded = False
        self.redact_classes = []
        self.redaction_spans = []


class Guard:
    """Records every inspect() call; verdict decided by a trigger substring."""

    def __init__(self, trigger=None, action="allow", threat="credential"):
        self.trigger, self.action, self.threat = trigger, action, threat
        self.lengths = []

    async def inspect(self, text, **_kw):
        self.lengths.append(len(text))
        if self.trigger and self.trigger in text:
            return V(self.action, self.threat)
        return V("allow")


def content_of(frame):
    line = frame.strip()
    if not line.startswith("data: ") or line == "data: [DONE]":
        return None
    try:
        d = json.loads(line[6:])
    except Exception:
        return None
    if "error" in d:
        return ("ERROR", d["error"])
    return "".join((c.get("delta") or {}).get("content") or "" for c in d.get("choices") or [])


def stream(pieces, guard):
    return SecureStreamingResponse(upstream(pieces), scanner=None, redaction_enabled=True,
                                   buffer_max_bytes=4096, max_buffer_chunks=64, output_guard=guard,
                                   enforcement_mode="block")


def prose_tokens(n, seed_sentence="The quarterly report shows steady growth across all regions this year. "):
    # split a repeating English text into ~token-sized pieces (word + trailing space)
    words = (seed_sentence * (n // 8 + 2)).split(" ")
    toks = [w + " " for w in words if w][:n]
    return toks


async def exp_a():
    print("=== A) streaming BLOCK after earlier flushes ===")
    toks = prose_tokens(300)
    secret_at = 200
    toks[secret_at] = "AKIAIOSFODNN7EXAMPLE "
    g = Guard(trigger="AKIA", action="block", threat="credential")
    delivered, error = [], None
    async for fr in stream(toks, g).__aiter__():
        c = content_of(fr)
        if isinstance(c, tuple):
            error = c[1]
            break
        if c:
            delivered.append(c)
    text = "".join(delivered)
    upstream_before_secret = "".join(toks[:secret_at])
    print(f"upstream tokens=300, secret at token {secret_at}; guard calls={len(g.lengths)}")
    print(f"bytes RELEASED to client before error frame = {len(text)} "
          f"({len(text) / len(upstream_before_secret):.0%} of the {len(upstream_before_secret)} bytes preceding the secret)")
    print("error frame:", json.dumps(error))
    print("secret leaked?", "AKIA" in text, "| last 60 released chars:", repr(text[-60:]))


async def exp_b():
    print("=== B) guard passes per stream (STREAM_FLUSH_BYTES=%d) ===" % STREAM_FLUSH_BYTES)
    cases = {
        "300 x 6-byte deltas, no sentence boundary": ["abcde "] * 300,
        "300 prose tokens (period every 12 words)": prose_tokens(300),
        "300 markdown tokens (newline every 8 tokens)": [("item " if (i + 1) % 8 else "item\n") for i in range(300)],
    }
    for label, toks in cases.items():
        g = Guard()
        async for _ in stream(toks, g).__aiter__():
            pass
        print(f"{label:48} bytes={sum(len(t) for t in toks):5} guard calls={len(g.lengths):3} "
              f"bytes scanned={sum(g.lengths)}")


async def exp_c():
    print("=== C) rewrite verdict mid-stream: buffer never cleared until DONE ===")
    for n in (300, 1000, 2000, 4000):
        toks = ["abcde "] * n
        toks[10] = "SSN-TRIGGER "
        g = Guard(trigger="SSN-TRIGGER", action="rewrite", threat="pii")
        out = []
        async for fr in stream(toks, g).__aiter__():
            c = content_of(fr)
            if c:
                out.append(c)
        print(f"n={n:5} deltas ({sum(len(t) for t in toks):6} B): guard calls={len(g.lengths):5} "
              f"max buffer scanned={max(g.lengths):6} B  total bytes re-scanned={sum(g.lengths):9}  "
              f"first 5 lengths={g.lengths[:5]} last 3={g.lengths[-3:]}  client got {len(''.join(out))} B at DONE")


async def main():
    await exp_a()
    await exp_b()
    await exp_c()


asyncio.run(main())
