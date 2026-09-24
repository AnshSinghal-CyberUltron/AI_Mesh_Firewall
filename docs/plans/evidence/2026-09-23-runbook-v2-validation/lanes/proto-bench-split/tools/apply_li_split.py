#!/usr/bin/env python3
"""LOOP-ISOLATION EXPERIMENT knobs (proto-bench-unit li-* runs; capacity measurement only) for a COPY of the
unit-1 rvproto tree. Every knob defaults to the unchanged behaviour.
proto-bench-split port (tools/apply_li_split.py): identical edits; the only changes are the config.py anchor,
which in rvproto-frozen-1 also lists the split topology: guard_topology not in ("in_process", "owner", "remote").
and the owner.py import anchor (rvproto-frozen-1's owner imports socket and already imports time).

  RV_METRICS_DUMP_S      periodic metrics-file dump interval in seconds (default 1 = unchanged);
                         0 = no periodic dump; workers and guard owners dump only on SIGUSR1 (the unit sampler
                         sends it at the measurement-window boundaries)
  RV_TOKENIZE_IN_THREAD  1 = PG2 tokenization runs as Tokenizer.encode_batch([text]) in a per-worker
                         ThreadPoolExecutor (encode_batch releases the GIL, encode does not: loop-isolation/
                         gil_test_unit1.txt); ids are identical to encode()
  RV_HOLDBACK            off = "scan-no-holdback, NOT boundary-safe, capacity measurement only": the streaming
                         output scan still runs on every chunk (same detectors) but each chunk is released right
                         after scanning; a pattern split across chunks can leak
Also: a SIGUSR1 handler (on-demand metrics dump) in workers and owners.

  apply_li.py ROOT     (idempotent; each edit anchors on whole lines that must occur exactly once)
"""

from __future__ import annotations

import sys
from pathlib import Path


def one(text: str, old: str, path: Path) -> None:
    if text.count(old) != 1:
        raise SystemExit(f"{path}: expected exactly one occurrence of {old!r}")


def swap(text: str, old: str, new: str, path: Path) -> str:
    one(text, old, path)
    return text.replace(old, new, 1)


def main() -> int:
    root = Path(sys.argv[1])
    p = root / "rvproto/runtime/config.py"
    s = p.read_text()
    if "RV_METRICS_DUMP_S" not in s:
        s = swap(s, '    ("RV_GUARD_SOCKET_DIR", ""),  # empty => $XDG_RUNTIME_DIR/rv-guard-<port> (sun_path is 108 bytes)\n',
                 '    ("RV_GUARD_SOCKET_DIR", ""),  # empty => $XDG_RUNTIME_DIR/rv-guard-<port> (sun_path is 108 bytes)\n'
                 "    # LOOP-ISOLATION EXPERIMENT (proto-bench-unit li-*, capacity measurement only; defaults = unchanged)\n"
                 '    ("RV_METRICS_DUMP_S", "1"),  # periodic metrics dump interval; 0 = only on SIGUSR1\n'
                 '    ("RV_TOKENIZE_IN_THREAD", "0"),  # 1 = encode_batch (releases the GIL) in a thread pool\n'
                 '    ("RV_TOKENIZE_THREADS", "2"),  # thread-pool size per worker when RV_TOKENIZE_IN_THREAD=1\n'
                 '    ("RV_HOLDBACK", "on"),  # off = scan-no-holdback, NOT boundary-safe\n', p)
        s = swap(s, "    guard_socket_dir: str\n",
                 "    guard_socket_dir: str\n    metrics_dump_s: float\n    tokenize_in_thread: bool\n    tokenize_threads: int\n"
                 "    holdback: bool\n", p)
        s = swap(s, '        guard_socket_dir=get["RV_GUARD_SOCKET_DIR"] or _socket_dir(src, get["RV_PORT"]),\n',
                 '        guard_socket_dir=get["RV_GUARD_SOCKET_DIR"] or _socket_dir(src, get["RV_PORT"]),\n'
                 '        metrics_dump_s=float(get["RV_METRICS_DUMP_S"]),\n'
                 '        tokenize_in_thread=get["RV_TOKENIZE_IN_THREAD"] == "1",\n'
                 '        tokenize_threads=int(get["RV_TOKENIZE_THREADS"]),\n'
                 '        holdback=get["RV_HOLDBACK"].strip().lower() != "off",\n', p)
        s = swap(s, '    if s.guard_topology not in ("in_process", "owner", "remote"):\n',
                 '    if get["RV_HOLDBACK"].strip().lower() not in ("on", "off"):\n'
                 "        raise ValueError(f\"RV_HOLDBACK={get['RV_HOLDBACK']!r} (on | off)\")\n"
                 '    if s.guard_topology not in ("in_process", "owner", "remote"):\n', p)
        p.write_text(s)

    p = root / "rvproto/edge/app.py"
    s = p.read_text()
    if "SIGUSR1" not in s:
        s = swap(s, "import contextlib\nimport time\n", "import contextlib\nimport signal\nimport time\n", p)
        s = swap(s, "    async def _dump_metrics(self, st: State) -> None:\n"
                    "        while True:\n"
                    "            await asyncio.sleep(1.0)\n"
                    "            ops._gauges(st)\n"
                    "            st.metrics.dump(st.s.metrics_dir)\n",
                 "    async def _dump_metrics(self, st: State) -> None:\n"
                 "        def dump() -> None:\n"
                 "            ops._gauges(st)\n"
                 "            st.metrics.dump(st.s.metrics_dir)\n\n"
                 "        # li experiment: SIGUSR1 = on-demand dump; RV_METRICS_DUMP_S=0 disables the periodic dump\n"
                 "        asyncio.get_running_loop().add_signal_handler(signal.SIGUSR1, dump)\n"
                 "        if st.s.metrics_dump_s <= 0:\n"
                 "            await asyncio.Event().wait()\n"
                 "        while True:\n"
                 "            await asyncio.sleep(st.s.metrics_dump_s)\n"
                 "            dump()\n", p)
        p.write_text(s)

    p = root / "rvproto/detect/guard/owner.py"
    s = p.read_text()
    if "SIGUSR1" not in s:
        s = swap(s, "import os\nimport socket\nimport sys\nimport time\n", "import os\nimport signal\nimport socket\nimport sys\nimport time\n", p)
        s = swap(s, "    async def _gauges(self) -> None:\n        while True:\n",
                 "    async def _gauges(self) -> None:\n"
                 "        def dump() -> None:\n"
                 "            if self.s.metrics_dir:\n"
                 "                self.metrics.dump(self.s.metrics_dir, stem=\"owner\")\n\n"
                 "        asyncio.get_running_loop().add_signal_handler(signal.SIGUSR1, dump)  # li experiment\n"
                 "        last = 0.0\n"
                 "        while True:\n", p)
        s = swap(s, "            if self.s.metrics_dir:\n"
                    "                m.dump(self.s.metrics_dir, stem=\"owner\")\n"
                    "            await asyncio.sleep(1.0)\n",
                 "            if self.s.metrics_dump_s > 0 and time.monotonic() - last >= self.s.metrics_dump_s:\n"
                 "                dump()\n"
                 "                last = time.monotonic()\n"
                 "            await asyncio.sleep(1.0)\n", p)
        p.write_text(s)

    p = root / "rvproto/detect/semantic.py"
    s = p.read_text()
    if "batch_api" not in s:
        s = swap(s, "    def windows(self, texts: Sequence[str]) -> Windows:\n"
                    "        ids = self.tok.encode(\"\\n\".join(texts), add_special_tokens=False).ids\n",
                 "    def windows(self, texts: Sequence[str], batch_api: bool = False) -> Windows:\n"
                 "        joined = \"\\n\".join(texts)\n"
                 "        # li experiment: encode_batch releases the GIL (encode does not); identical ids\n"
                 "        enc = (self.tok.encode_batch([joined], add_special_tokens=False)[0] if batch_api\n"
                 "               else self.tok.encode(joined, add_special_tokens=False))\n"
                 "        ids = enc.ids\n", p)
        p.write_text(s)

    p = root / "rvproto/edge/chat.py"
    s = p.read_text()
    if "tok_pool" not in s:
        s = swap(s, "                windows = st.sem.windows([c.text for c in canon])\n",
                 "                if st.tok_pool is not None:  # li experiment: tokenization off the event loop\n"
                 "                    windows = await asyncio.get_running_loop().run_in_executor(\n"
                 "                        st.tok_pool, st.sem.windows, [c.text for c in canon], True)\n"
                 "                else:\n"
                 "                    windows = st.sem.windows([c.text for c in canon])\n", p)
        p.write_text(s)

    p = root / "rvproto/edge/state.py"
    s = p.read_text()
    if "tok_pool" not in s:
        s = swap(s, "import sys\nimport time\n", "import sys\nimport time\nfrom concurrent.futures import ThreadPoolExecutor\n", p)
        s = swap(s, "    inspectors: dict[tuple[str, str], PlanInspector] = field(default_factory=dict)\n",
                 "    inspectors: dict[tuple[str, str], PlanInspector] = field(default_factory=dict)\n"
                 "    tok_pool: ThreadPoolExecutor | None = None  # li experiment (RV_TOKENIZE_IN_THREAD=1)\n", p)
        s = swap(s, "               Verifier(matcher, s.decode_budget), guard, sem, audit, provider, deadline, gate)\n",
                 "               Verifier(matcher, s.decode_budget), guard, sem, audit, provider, deadline, gate)\n"
                 "    if s.tokenize_in_thread:\n"
                 "        st.tok_pool = ThreadPoolExecutor(max_workers=s.tokenize_threads, thread_name_prefix=\"rv-tok\")\n", p)
        p.write_text(s)

    p = root / "rvproto/egress/stream.py"
    s = p.read_text()
    if "self.holdback" not in s:
        s = swap(s, "                 inject_hold: tuple[int, float] | None) -> None:\n",
                 "                 inject_hold: tuple[int, float] | None, holdback: bool = True) -> None:\n", p)
        s = swap(s, "        self.inject_hold = inject_hold\n",
                 "        self.inject_hold = inject_hold\n"
                 "        self.holdback = holdback  # li experiment: False = scan-no-holdback (NOT boundary-safe)\n", p)
        s = swap(s, "        h = len(buf) if final else self.inspector.hold_start(buf)\n",
                 "        h = len(buf) if (final or not self.holdback) else self.inspector.hold_start(buf)\n", p)
        p.write_text(s)

    p = root / "rvproto/edge/respond.py"
    s = p.read_text()
    if "holdback=st.s.holdback" not in s:
        s = swap(s, "                          inject_hold=st.s.inject_hold)\n",
                 "                          inject_hold=st.s.inject_hold, holdback=st.s.holdback)\n", p)
        p.write_text(s)
    print(f"li knobs applied to {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
