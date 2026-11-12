"""Shared helpers for the F2 guard-model prototype (PG2-22M fp32 ONNX).

Prototype code for the design plan only; NOT product code. Everything a production stage would
need to reproduce lives here so the parity, benchmark and evaluation scripts agree on ONE
tokenizer configuration, ONE windowing rule and ONE session configuration.

Paths come from the environment so nothing host-specific is committed:
  F2_MODEL_DIR  directory with model.safetensors, tokenizer.json, config.json (pristine HF bytes)
  F2_OUT_DIR    scratch directory for the exported ONNX file and downloaded datasets (not in git)
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

MODEL_DIR = Path(os.environ.get("F2_MODEL_DIR", "/home/claude/work/models/pg2-22m"))
OUT_DIR = Path(os.environ.get("F2_OUT_DIR", "/home/claude/work/wt-f2/.f2-scratch/out"))
ONNX_PATH = OUT_DIR / "pg2-22m-fp32-opset17.onnx"
EVIDENCE_DIR = Path(__file__).resolve().parent.parent

#: Model context: 512 positions = [CLS] + 510 payload tokens + [SEP].
MAX_POSITIONS = 512
PAYLOAD = MAX_POSITIONS - 2


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_tokenizer(path: Path | None = None):  # -> tokenizers.Tokenizer
    """The shipped tokenizer.json with its baked-in truncation(512) and padding(512) DISABLED.

    Left on, ``encode`` silently drops every token after 512 and pads every input to 512.
    """
    from tokenizers import Tokenizer

    tok = Tokenizer.from_file(str(path or MODEL_DIR / "tokenizer.json"))
    tok.no_truncation()
    tok.no_padding()
    return tok


@dataclass(frozen=True, slots=True)
class Window:
    """One model input row and the char span of the original text it covers."""

    ids: tuple[int, ...]  # [CLS] + payload + [SEP]
    char_start: int
    char_end: int


@dataclass(frozen=True, slots=True)
class WindowPlan:
    windows: tuple[Window, ...]
    n_tokens: int  # payload tokens of the whole text (no specials)


class TooManyWindows(ValueError):
    def __init__(self, required: int, maximum: int) -> None:
        super().__init__(f"{required} windows > max {maximum}")
        self.required = required
        self.maximum = maximum


def required_windows(n_tokens: int, payload: int = PAYLOAD, overlap: int = 64) -> int:
    if n_tokens <= payload:
        return 1
    stride = payload - overlap
    return 1 + -(-(n_tokens - payload) // stride)


def plan_windows(
    tok,
    text: str,
    *,
    payload: int = PAYLOAD,
    overlap: int = 64,
    max_windows: int | None = None,
) -> WindowPlan:
    """Tokenize ONCE, then slice the id stream into [CLS]+payload+[SEP] rows with overlap.

    Token-based (not chars-per-token): every row is <= 512 positions by construction, whatever the
    script. Over ``max_windows`` raises :class:`TooManyWindows` (an explicit length outcome, never a
    silent head/tail cut).
    """
    cls_id = tok.token_to_id("[CLS]")
    sep_id = tok.token_to_id("[SEP]")
    enc = tok.encode(text, add_special_tokens=False)
    ids = enc.ids
    offsets = enc.offsets
    n = len(ids)
    need = required_windows(n, payload, overlap)
    if max_windows is not None and need > max_windows:
        raise TooManyWindows(need, max_windows)
    if n == 0:
        return WindowPlan((Window((cls_id, sep_id), 0, 0),), 0)
    stride = payload - overlap
    rows: list[Window] = []
    start = 0
    while True:
        end = min(start + payload, n)
        rows.append(
            Window(
                (cls_id, *ids[start:end], sep_id),
                offsets[start][0],
                offsets[end - 1][1],
            )
        )
        if end >= n:
            break
        start += stride
    return WindowPlan(tuple(rows), n)


def make_session(path: Path | None = None, *, threads: int = 1, spinning: bool = False):
    """The ONE session configuration the plan proposes for the CPU stage."""
    import onnxruntime as ort

    opts = ort.SessionOptions()
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    opts.intra_op_num_threads = threads
    opts.inter_op_num_threads = 1
    flag = "1" if spinning else "0"
    opts.add_session_config_entry("session.intra_op.allow_spinning", flag)
    opts.add_session_config_entry("session.inter_op.allow_spinning", flag)
    sess = ort.InferenceSession(
        str(path or ONNX_PATH), sess_options=opts, providers=["CPUExecutionProvider"]
    )
    sess.disable_fallback()
    if sess.get_providers()[0] != "CPUExecutionProvider":
        raise RuntimeError(f"unexpected providers {sess.get_providers()}")
    return sess


def run_rows(sess, rows: Sequence[Sequence[int]]) -> np.ndarray:
    """Run rows of EQUAL length as one batch (no padding). Returns logits [n, 2]."""
    width = len(rows[0])
    if any(len(r) != width for r in rows):
        raise ValueError("run_rows takes equal-length rows; group by length first")
    ids = np.asarray(rows, dtype=np.int64)
    mask = np.ones_like(ids)
    return sess.run(["logits"], {"input_ids": ids, "attention_mask": mask})[0]


def run_padded(sess, rows: Sequence[Sequence[int]], pad_to: int | None = None) -> np.ndarray:
    """Run rows of mixed length right-padded with [PAD]=0 + attention mask (for the drift test)."""
    width = pad_to or max(len(r) for r in rows)
    ids = np.zeros((len(rows), width), dtype=np.int64)
    mask = np.zeros((len(rows), width), dtype=np.int64)
    for i, row in enumerate(rows):
        ids[i, : len(row)] = row
        mask[i, : len(row)] = 1
    return sess.run(["logits"], {"input_ids": ids, "attention_mask": mask})[0]


def p_malicious(logits: np.ndarray) -> np.ndarray:
    """softmax(logits)[:, 1] in float64 (label 1 = MALICIOUS for PG2)."""
    z = logits.astype(np.float64)
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e[:, 1] / e.sum(axis=1)


def score_plan(sess, plan: WindowPlan) -> np.ndarray:
    """Per-window P(malicious), grouping equal-length rows into one unpadded batch."""
    by_len: dict[int, list[int]] = {}
    for i, w in enumerate(plan.windows):
        by_len.setdefault(len(w.ids), []).append(i)
    out = np.empty(len(plan.windows), dtype=np.float64)
    for idx in by_len.values():
        out[idx] = p_malicious(run_rows(sess, [plan.windows[i].ids for i in idx]))
    return out


def batched(seq: Sequence, n: int) -> Iterator[Sequence]:
    for i in range(0, len(seq), n):
        yield seq[i : i + n]
