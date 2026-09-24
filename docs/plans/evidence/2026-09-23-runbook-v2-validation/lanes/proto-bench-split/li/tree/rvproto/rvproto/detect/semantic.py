"""Semantic injection detection (PG2) over ALL windows of the input.

Tokenize once with the model's own tokenizer; W-token windows ([CLS] + W-2 + [SEP])
with a declared overlap; no truncation: more windows than the declared maximum
is an explicit length rejection, never a silent head/tail cut.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from tokenizers import Tokenizer

from rvproto.domain.findings import (
    DETECTORS,
    SEMANTIC_DETECTOR,
    Finding,
    FindingStatus,
    unavailable,
)
from rvproto.domain.guard import Budget, GuardBackend, GuardResult


class InputTooLong(ValueError):
    def __init__(self, n_tokens: int, max_tokens: int) -> None:
        super().__init__(f"input is {n_tokens} guard tokens; maximum is {max_tokens}")
        self.n_tokens = n_tokens
        self.max_tokens = max_tokens


@dataclass(frozen=True, slots=True)
class Windows:
    rows: tuple[list[int], ...]
    n_tokens: int


def file_hash(path: str) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


class SemanticDetector:
    def __init__(
        self,
        tokenizer_path: str,
        backend: GuardBackend,
        *,
        window: int,
        overlap: int,
        max_windows: int,
        model_hash: str,
    ) -> None:
        self.tok = Tokenizer.from_file(tokenizer_path)
        # The shipped PG2 tokenizer.json has truncation(512) and padding baked in:
        # left on, encode() silently drops every token after 512 (the v1 hole).
        self.tok.no_truncation()
        self.tok.no_padding()
        self.cls = self.tok.token_to_id("[CLS]")
        self.sep = self.tok.token_to_id("[SEP]")
        self.payload = window - 2
        self.stride = self.payload - overlap
        self.max_windows = max_windows
        self.max_tokens = self.payload + (max_windows - 1) * self.stride
        self.backend = backend
        self.version = f"pg2-{model_hash[:12]}-tok-{file_hash(tokenizer_path)[:12]}"

    def windows(self, texts: Sequence[str], batch_api: bool = False) -> Windows:
        joined = "\n".join(texts)
        # li experiment: encode_batch releases the GIL (encode does not); identical ids
        enc = (self.tok.encode_batch([joined], add_special_tokens=False)[0] if batch_api
               else self.tok.encode(joined, add_special_tokens=False))
        ids = enc.ids
        n = len(ids)
        if n > self.max_tokens:
            raise InputTooLong(n, self.max_tokens)
        rows: list[list[int]] = []
        start = 0
        while True:
            rows.append([self.cls, *ids[start : start + self.payload], self.sep])
            if start + self.payload >= n:
                break
            start += self.stride
        return Windows(tuple(rows), n)

    def submit(self, w: Windows, deadline_ms: float) -> asyncio.Future[GuardResult]:
        budget = Budget(time.perf_counter_ns() + int(deadline_ms * 1e6))
        return self.backend.submit(w.rows, budget)

    def finding(self, w: Windows, result: GuardResult) -> Finding:
        if not result.ok:
            return unavailable(SEMANTIC_DETECTOR, self.version, result.detail)
        probs = result.p_malicious
        k = max(range(len(probs)), key=probs.__getitem__)
        return Finding(
            SEMANTIC_DETECTOR,
            self.version,
            DETECTORS[SEMANTIC_DETECTOR],
            FindingStatus.EXECUTED,
            float(probs[k]),
            (),
            f"windows={len(probs)} tokens={w.n_tokens} argmax_window={k} p={probs[k]:.4f}",
        )

    def unavailable(self, why: str) -> Finding:
        return unavailable(SEMANTIC_DETECTOR, self.version, why)
