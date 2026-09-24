"""SSE egress: incremental upstream parse -> streaming output scan with PATTERN-AWARE
MINIMAL HOLDBACK -> resolve(OUTPUT) per released piece -> re-serialized OpenAI chunks.

Per text stream (choice content, each tool call's arguments) only the minimal suffix
that could still become a match is held; everything before it is scanned, redacted
per the plan and released immediately. Chunks keep id/model/created/role/tool_call
fragments/finish_reason/usage. Backpressure = await send(). Per released piece we
record processing lag (compute after the bytes arrived) and holdback wait (time the
oldest released char waited for more upstream bytes) SEPARATELY.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import aiohttp
import orjson

from rvproto.domain.decision import Decision, Disposition
from rvproto.domain.ports import Hit, OutputInspector
from rvproto.domain.text import redact_text
from rvproto.egress.sse import DONE, DONE_FRAME, SseParser, error_frame, frame
from rvproto.runtime.metrics import Registry

Send = Callable[[bytes], Awaitable[None]]


class OutputBlocked(Exception):
    def __init__(self, decision: Decision) -> None:
        super().__init__("output blocked")
        self.decision = decision


class HoldbackOverflow(Exception):
    pass


@dataclass(slots=True)
class _Text:
    pending: str = ""
    held_since: int = 0
    ctx: str = ""
    released: int = 0
    hits: list[Hit] = field(default_factory=list)


@dataclass(slots=True)
class StreamStats:
    upstream_chunks: int = 0
    frames_out: int = 0
    scans: int = 0
    first_ready_ns: int = 0
    last_ready_ns: int = 0
    release_lag_max_ns: int = 0
    done: bool = False
    error: str | None = None
    texts: dict[tuple[Any, ...], _Text] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)


class StreamPipeline:
    def __init__(self, inspector: OutputInspector, metrics: Registry, *, ceiling: int,
                 inject_hold: tuple[int, float] | None, holdback: bool = True) -> None:
        self.inspector = inspector
        self.metrics = metrics
        self.ceiling = ceiling
        self.inject_hold = inject_hold
        self.holdback = holdback  # li experiment: False = scan-no-holdback (NOT boundary-safe)
        self.stats = StreamStats()  # partial state stays inspectable after cancellation

    async def run(self, resp: aiohttp.ClientResponse, send: Send) -> StreamStats:
        st = self.stats
        parser = SseParser()
        try:
            async for data in resp.content.iter_any():
                t_ready = time.perf_counter_ns()
                st.last_ready_ns = t_ready
                if not st.first_ready_ns:
                    st.first_ready_ns = t_ready
                for payload in parser.feed(data):
                    if payload == DONE:
                        await self._emit_all(self._flush(st, t_ready), send, st, t_ready)
                        await send(DONE_FRAME)
                        st.done = True
                        return st
                    await self._on_chunk(orjson.loads(payload), st, send, t_ready)
        except OutputBlocked as ob:
            st.error = "output_blocked"
            await send(error_frame(f"output blocked by rules {ob.decision.deciding_rules}", "output_blocked"))
            return st
        except HoldbackOverflow:
            st.error = "holdback_overflow"
            self.metrics.inc("holdback_overflow")
            await send(error_frame("output holdback exceeded its contract bound", "holdback_overflow"))
            return st
        except (orjson.JSONDecodeError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
            st.error = f"upstream_protocol_error:{type(exc).__name__}"
        if st.error is None:
            st.error = "upstream_ended_without_done"
        self.metrics.inc("upstream_stream_errors")
        await send(error_frame("upstream stream ended abnormally", "upstream_stream_error"))
        return st

    async def _on_chunk(self, chunk: dict[str, Any], st: StreamStats, send: Send, t_ready: int) -> None:
        st.upstream_chunks += 1
        st.meta = chunk
        pieces: list[tuple[int, int]] = []  # (released chars, oldest arrival ns)
        frames = self._process(chunk, st, t_ready, pieces)
        hold = self.inject_hold
        if hold is not None and st.upstream_chunks == hold[0]:
            await asyncio.sleep(hold[1] / 1000.0)  # instrument-honesty fault injection
        await self._emit_all(frames, send, st, t_ready, pieces)

    async def _emit_all(self, frames: list[bytes], send: Send, st: StreamStats, t_ready: int,
                        pieces: list[tuple[int, int]] | None = None) -> None:
        for f in frames:
            await send(f)
            st.frames_out += 1
        if not pieces:
            return
        t_sent = time.perf_counter_ns()
        self.metrics.observe("release_processing_ns", t_sent - t_ready)
        for n, oldest in pieces:
            if n == 0:
                continue
            wait = t_ready - oldest
            if wait > 0:
                self.metrics.observe("holdback_wait_ns", wait)
            lag = t_sent - oldest
            self.metrics.observe("release_lag_ns", lag)
            if lag > st.release_lag_max_ns:
                st.release_lag_max_ns = lag

    def _process(self, chunk: dict[str, Any], st: StreamStats, t_ready: int,
                 pieces: list[tuple[int, int]]) -> list[bytes]:
        choices = chunk.get("choices")
        if not choices:
            return [frame(chunk)]
        out: list[bytes] = []
        new_choices = []
        emit = bool(chunk.get("usage"))
        for c in choices:
            i = c.get("index", 0)
            final = c.get("finish_reason") is not None
            delta = c.get("delta") or {}
            nd = dict(delta)
            fed: set[tuple[Any, ...]] = set()
            content = delta.get("content")
            if isinstance(content, str) and content:
                key = ("c", i)
                nd["content"] = self._feed(st, key, content, t_ready, final, pieces)
                fed.add(key)
            tcs = delta.get("tool_calls")
            if tcs:
                nd["tool_calls"] = [self._tool(st, i, tc, t_ready, final, pieces, fed) for tc in tcs]
            if final:
                extra = self._flush_choice(st, i, t_ready, fed, pieces)
                if extra is not None:
                    base = {k: v for k, v in chunk.items() if k not in ("choices", "usage")}
                    base["choices"] = [{"index": i, "delta": extra, "finish_reason": None}]
                    out.append(frame(base))
            emit = emit or final or len(nd) > 1 or bool(nd.get("content")) or "content" not in nd
            nc = dict(c)
            nc["delta"] = nd
            new_choices.append(nc)
        if emit:
            nchunk = dict(chunk)
            nchunk["choices"] = new_choices
            out.append(frame(nchunk))
        return out

    def _tool(self, st: StreamStats, i: int, tc: dict[str, Any], t_ready: int, final: bool,
              pieces: list[tuple[int, int]], fed: set[tuple[Any, ...]]) -> dict[str, Any]:
        fn = tc.get("function") or {}
        args = fn.get("arguments")
        if not (isinstance(args, str) and args):
            return tc
        key = ("t", i, tc.get("index", 0))
        nfn = dict(fn)
        nfn["arguments"] = self._feed(st, key, args, t_ready, final, pieces)
        fed.add(key)
        ntc = dict(tc)
        ntc["function"] = nfn
        return ntc

    def _flush_choice(self, st: StreamStats, i: int, t_ready: int, fed: set[tuple[Any, ...]],
                      pieces: list[tuple[int, int]]) -> dict[str, Any] | None:
        extra: dict[str, Any] = {}
        for key, t in st.texts.items():
            if key[1] != i or key in fed or not t.pending:
                continue
            text = self._feed(st, key, "", t_ready, True, pieces)
            if key[0] == "c":
                extra["content"] = text
            else:
                extra.setdefault("tool_calls", []).append({"index": key[2], "function": {"arguments": text}})
        return extra or None

    def _flush(self, st: StreamStats, t_ready: int) -> list[bytes]:
        indices = sorted({k[1] for k, t in st.texts.items() if t.pending})
        out = []
        for i in indices:
            extra = self._flush_choice(st, i, t_ready, set(), [])
            if extra is not None:
                base = {k: v for k, v in st.meta.items() if k not in ("choices", "usage")}
                base["choices"] = [{"index": i, "delta": extra, "finish_reason": None}]
                out.append(frame(base))
        return out

    def _feed(self, st: StreamStats, key: tuple[Any, ...], text: str, t_ready: int, final: bool,
              pieces: list[tuple[int, int]]) -> str:
        t = st.texts.get(key)
        if t is None:
            t = st.texts[key] = _Text(held_since=t_ready)
        old = t.pending
        buf = old + text
        h = len(buf) if (final or not self.holdback) else self.inspector.hold_start(buf)
        off = len(t.ctx)
        st.scans += 1
        hits = [(d, max(s - off, 0), e - off) for d, s, e in self.inspector.hits(t.ctx + buf) if e > off]
        moved = True
        while moved:
            moved = False
            for _, s, e in hits:
                if s < h < e:
                    h = s
                    moved = True
        release = buf[:h]
        t.pending = buf[h:]
        oldest = t.held_since if (old and h > 0) else t_ready
        if t.pending and (not old or h >= len(old)):
            t.held_since = t_ready
        if len(t.pending) > self.ceiling:
            raise HoldbackOverflow()
        if not release:
            return ""
        pieces.append((len(release), oldest))
        done_hits = [(d, s, e) for d, s, e in hits if e <= h]
        out = release
        if done_hits:
            decision = self.inspector.decide([done_hits])
            if decision.disposition is Disposition.BLOCK:
                raise OutputBlocked(decision)
            if decision.transformations:
                out = redact_text(release, [(x.span.start, x.span.end, x.replacement)
                                            for x in decision.transformations])
            t.hits.extend((d, s + t.released, e + t.released) for d, s, e in done_hits)
        t.released += h
        t.ctx = buf[h - 1]
        return out

    def final_decision(self, st: StreamStats) -> Decision:
        """The ONE output-phase decision for the audit record (all released hits)."""
        return self.inspector.decide([t.hits for t in st.texts.values()])
