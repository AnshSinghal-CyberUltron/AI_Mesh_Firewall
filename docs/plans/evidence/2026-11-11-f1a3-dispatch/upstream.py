"""A minimal, fast, recording OpenAI-compatible upstream for the F1a-3 dispatch benchmark.

Prototype, not product code. It exists so the benchmark measures the GATEWAY'S client cost, not
the upstream's: responses are pre-rendered bytes, parsing is the minimum HTTP/1.1 needed
(request line, headers, Content-Length body), and connections are kept alive.

Records, per process: requests served, requests with a wrong bearer (answered 401 like a
provider), body bytes, and the per-run nonce found in bodies (so a client can check that every
request it sent arrived exactly once). `GET /stats` returns the counters as JSON.

    python upstream.py --port 18950 --chunks 20 [--tls] [--host 127.0.0.1]

The expected bearer is read from the environment variable named by --key-env (default
F1A3_BENCH_KEY). It is a random test value the benchmark driver generates; never a real key.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import ssl
import sys
import tempfile

_NONCE = re.compile(rb"nonce-[0-9a-f]{16}")
_STREAM = re.compile(rb'"stream"\s*:\s*true')


def _sse_body(chunks: int) -> bytes:
    parts = []
    for i in range(chunks):
        delta = {"content": f"tok{i} "} if i else {"role": "assistant", "content": "tok0 "}
        event = {
            "id": "chatcmpl-bench",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "bench-model",
            "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
        }
        parts.append(b"data: " + json.dumps(event, separators=(",", ":")).encode() + b"\n\n")
    last = {
        "id": "chatcmpl-bench",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "bench-model",
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    parts.append(b"data: " + json.dumps(last, separators=(",", ":")).encode() + b"\n\n")
    usage = {
        "id": "chatcmpl-bench",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "bench-model",
        "choices": [],
        "usage": {"prompt_tokens": 9, "completion_tokens": chunks, "total_tokens": 9 + chunks},
    }
    parts.append(b"data: " + json.dumps(usage, separators=(",", ":")).encode() + b"\n\n")
    parts.append(b"data: [DONE]\n\n")
    return b"".join(parts)


def _chunked(body: bytes, pieces: int) -> bytes:
    """Chunked transfer encoding, one HTTP chunk per SSE event (what real providers do)."""
    events = [e + b"\n\n" for e in body.split(b"\n\n") if e]
    out = bytearray()
    for event in events[: max(pieces, 1)]:
        out += f"{len(event):x}\r\n".encode() + event + b"\r\n"
    rest = b"".join(events[max(pieces, 1):])
    if rest:
        out += f"{len(rest):x}\r\n".encode() + rest + b"\r\n"
    out += b"0\r\n\r\n"
    return bytes(out)


class Stats:
    def __init__(self) -> None:
        self.ok = 0
        self.unauthorized = 0
        self.body_bytes = 0
        self.nonces: dict[bytes, int] = {}


def build_responses(chunks: int) -> dict[str, bytes]:
    sse = _chunked(_sse_body(chunks), chunks + 3)
    sse_head = (
        b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\n"
        b"transfer-encoding: chunked\r\ncache-control: no-cache\r\n\r\n"
    )
    completion = json.dumps(
        {
            "id": "chatcmpl-bench",
            "object": "chat.completion",
            "created": 1,
            "model": "bench-model",
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": "pong"},
                 "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 9, "completion_tokens": 1, "total_tokens": 10},
        },
        separators=(",", ":"),
    ).encode()
    json_resp = (
        b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: "
        + str(len(completion)).encode() + b"\r\n\r\n" + completion
    )
    err = b'{"error":{"message":"invalid key","type":"invalid_request_error"}}'
    unauth = (
        b"HTTP/1.1 401 Unauthorized\r\ncontent-type: application/json\r\ncontent-length: "
        + str(len(err)).encode() + b"\r\n\r\n" + err
    )
    return {"sse": sse_head + sse, "json": json_resp, "401": unauth}


class Upstream(asyncio.Protocol):
    def __init__(
        self, stats: Stats, responses: dict[str, bytes], bearer: bytes, delay_s: float = 0.0
    ) -> None:
        self.stats, self.responses, self.bearer = stats, responses, bearer
        self.delay_s = delay_s
        self.buf = bytearray()
        self.transport: asyncio.Transport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]  # uvloop's transport class

    def data_received(self, data: bytes) -> None:
        self.buf += data
        while True:
            end = self.buf.find(b"\r\n\r\n")
            if end < 0:
                return
            head = bytes(self.buf[:end]).decode("latin-1")
            lines = head.split("\r\n")
            headers = {}
            for line in lines[1:]:
                name, _, value = line.partition(":")
                headers[name.strip().lower()] = value.strip()
            length = int(headers.get("content-length", "0"))
            if len(self.buf) < end + 4 + length:
                return
            body = bytes(self.buf[end + 4: end + 4 + length])
            del self.buf[: end + 4 + length]
            self.handle(lines[0], headers, body)

    def handle(self, request_line: str, headers: dict[str, str], body: bytes) -> None:
        assert self.transport is not None
        method, path, _ = request_line.split(" ", 2)
        if method == "GET" and path == "/stats":
            payload = json.dumps(
                {
                    "ok": self.stats.ok,
                    "unauthorized": self.stats.unauthorized,
                    "body_bytes": self.stats.body_bytes,
                    "distinct_nonces": len(self.stats.nonces),
                    "duplicate_nonces": sum(1 for n in self.stats.nonces.values() if n > 1),
                }
            ).encode()
            self.transport.write(
                b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: "
                + str(len(payload)).encode() + b"\r\n\r\n" + payload
            )
            return
        auth = headers.get("authorization", "").encode()
        if auth != b"Bearer " + self.bearer:
            self.stats.unauthorized += 1
            self.transport.write(self.responses["401"])
            return
        self.stats.ok += 1
        self.stats.body_bytes += len(body)
        match = _NONCE.search(body)
        if match:
            self.stats.nonces[match.group()] = self.stats.nonces.get(match.group(), 0) + 1
        stream = _STREAM.search(body) is not None
        payload = self.responses["sse" if stream else "json"]
        if self.delay_s > 0:
            asyncio.get_running_loop().call_later(self.delay_s, self.transport.write, payload)
        else:
            self.transport.write(payload)


def self_signed(tmp: str) -> ssl.SSLContext:
    """A throwaway self-signed certificate for the TLS variant (test material only)."""
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "bench.upstream.test")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(hours=2))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("bench.upstream.test")]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = os.path.join(tmp, "cert.pem"), os.path.join(tmp, "key.pem")
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(key_path, "wb") as f:
        f.write(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_path, key_path)
    return ctx


async def serve(args: argparse.Namespace) -> None:
    bearer = os.environ.get(args.key_env, "").encode()
    if not bearer:
        raise SystemExit(f"{args.key_env} must be set")
    stats = Stats()
    responses = build_responses(args.chunks)
    loop = asyncio.get_running_loop()
    ssl_ctx = None
    if args.tls:
        tmp = tempfile.mkdtemp(prefix="f1a3-bench-")
        ssl_ctx = self_signed(tmp)
        with open(args.tls_ca_out, "wb") as dst, open(os.path.join(tmp, "cert.pem"), "rb") as src:
            dst.write(src.read())
    server = await loop.create_server(
        lambda: Upstream(stats, responses, bearer, args.delay_ms / 1000.0),
        args.host,
        args.port,
        ssl=ssl_ctx,
        backlog=4096,
    )
    print(f"upstream ready on {args.host}:{args.port} tls={args.tls}", flush=True)
    async with server:
        await server.serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18950)
    parser.add_argument("--chunks", type=int, default=20)
    parser.add_argument("--tls", action="store_true")
    parser.add_argument("--tls-ca-out", default="")
    parser.add_argument("--key-env", default="F1A3_BENCH_KEY")
    parser.add_argument("--delay-ms", type=float, default=0.0, help="hold each response")
    args = parser.parse_args()
    try:
        import uvloop

        uvloop.install()
    except ImportError:
        pass
    asyncio.run(serve(args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
