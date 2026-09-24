"""Guard-owner wire protocol (Unix socket or TCP): frames, validation, endpoints.

Frames (little-endian; `len` counts the bytes after itself):
  request   <I len> <I req_id> <q budget_ns> <H n> <H len_i>*n <i token>*sum(len_i)
  cancel    a request frame with n == 0 (the worker's client went away: skip if still queued)
  response  <I len> <I req_id> <B status> <q queue_ns> <q exec_ns> <H n> <f p>*n <H dlen> detail
            status 1 OK, 0 UNAVAILABLE, 2 SHED (owner queue full; queue_ns = retry-after ns)
  hello     the owner's first frame on every connection: a response with req_id 0 whose detail
            is JSON {host, index, pid, tokens_per_s, model_hash, provider} (worker ids start at 1)

budget_ns is the REMAINING guard budget when the worker wrote the frame, never an absolute
time: monotonic clocks of two hosts are not comparable. The owner re-anchors it on receipt, so
the transit (tens of microseconds in one zone) is not charged against the budget.

Both ends validate every frame against the declared window geometry before using it. The TCP
listener is unauthenticated, for VPC-internal traffic only (deploy/start_guard_node.sh): a
malformed or oversized frame closes the connection and never reaches the engine.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from rvproto.domain.guard import GuardResult

REQ_HEAD = struct.Struct("<IqH")
RESP_HEAD = struct.Struct("<IBqqH")
U16 = struct.Struct("<H")
U32 = struct.Struct("<I")
MAX_DETAIL = 512
OK, UNAVAILABLE, SHED = 1, 0, 2


class FrameError(ValueError):
    """A frame that does not match the protocol or the declared geometry."""


@dataclass(frozen=True, slots=True)
class Geometry:
    max_windows: int
    window_tokens: int
    vocab: int | None = None  # token ids must be < vocab when known (owner side)

    @property
    def max_request(self) -> int:
        return REQ_HEAD.size + self.max_windows * (2 + 4 * self.window_tokens)

    @property
    def max_response(self) -> int:
        return RESP_HEAD.size + 4 * self.max_windows + U16.size + MAX_DETAIL


@dataclass(frozen=True, slots=True)
class Endpoint:
    kind: str  # "unix" | "tcp"
    address: str  # socket path | host
    port: int = 0

    def __str__(self) -> str:
        return self.address if self.kind == "unix" else f"{self.address}:{self.port}"


def socket_path(socket_dir: str, index: int) -> str:
    return str(Path(socket_dir) / f"guard-{index}.sock")


def encode_request(req_id: int, budget_ns: int, rows: list[list[int]]) -> bytes:
    lens = np.fromiter((len(r) for r in rows), dtype="<u2", count=len(rows))
    ids = np.fromiter((t for r in rows for t in r), dtype="<i4", count=int(lens.sum()))
    body = REQ_HEAD.pack(req_id, budget_ns, len(rows)) + lens.tobytes() + ids.tobytes()
    return U32.pack(len(body)) + body


def decode_request(body: bytes, geo: Geometry) -> tuple[int, int, list[np.ndarray]]:
    if len(body) < REQ_HEAD.size:
        raise FrameError("short request frame")
    req_id, budget, n = REQ_HEAD.unpack_from(body, 0)
    if n > geo.max_windows:
        raise FrameError(f"{n} windows > {geo.max_windows}")
    lens = np.frombuffer(body, dtype="<u2", count=n, offset=REQ_HEAD.size)
    if n and (int(lens.min()) < 1 or int(lens.max()) > geo.window_tokens):
        raise FrameError("window length outside [1, window_tokens]")
    total = int(lens.sum(dtype=np.int64))
    if len(body) != REQ_HEAD.size + 2 * n + 4 * total:
        raise FrameError("request frame length does not match its windows")
    ids = np.frombuffer(body, dtype="<i4", offset=REQ_HEAD.size + 2 * n)
    if total and geo.vocab is not None and (int(ids.min()) < 0 or int(ids.max()) >= geo.vocab):
        raise FrameError("token id outside the model vocabulary")
    bounds = np.concatenate(([0], np.cumsum(lens, dtype=np.int64)))
    return req_id, budget, [ids[bounds[i] : bounds[i + 1]] for i in range(n)]


def encode_response(req_id: int, res: GuardResult) -> bytes:
    detail = res.detail.encode()[:MAX_DETAIL]
    probs = np.asarray(res.p_malicious, dtype="<f4").tobytes()
    if res.retry_after_s is not None:
        status, q_ns = SHED, int(res.retry_after_s * 1e9)
    else:
        status, q_ns = (OK if res.ok else UNAVAILABLE), res.queue_ns
    body = (RESP_HEAD.pack(req_id, status, q_ns, res.exec_ns, len(res.p_malicious))
            + probs + U16.pack(len(detail)) + detail)
    return U32.pack(len(body)) + body


def decode_response(body: bytes, geo: Geometry) -> tuple[int, GuardResult]:
    if len(body) < RESP_HEAD.size:
        raise FrameError("short response frame")
    req_id, status, q_ns, e_ns, n = RESP_HEAD.unpack_from(body, 0)
    off = RESP_HEAD.size + 4 * n
    if status not in (OK, UNAVAILABLE, SHED) or n > geo.max_windows or len(body) < off + U16.size:
        raise FrameError("malformed response frame")
    probs = tuple(float(x) for x in np.frombuffer(body, dtype="<f4", count=n, offset=RESP_HEAD.size))
    (dlen,) = U16.unpack_from(body, off)
    if len(body) != off + U16.size + dlen:
        raise FrameError("response frame length does not match its detail")
    detail = body[off + U16.size :].decode(errors="replace")
    if status == SHED:
        return req_id, GuardResult(False, (), 0, e_ns, 0, detail, retry_after_s=q_ns / 1e9)
    return req_id, GuardResult(status == OK, probs, q_ns, e_ns, n, detail)
