"""Recording upstream provider for the leak-hunt egress rig (story A2).

The PRIME INVARIANT of every leak story is *egress = truth*: a redact/block
verdict (or ``assume_redacted=True``) is valid ONLY if the bytes the gateway
actually sends upstream reflect it. ``pipeline_trace`` is a pre-redaction trace and
the client response is the model's output — neither proves what the *provider*
received. This module captures exactly that.

It monkeypatches the module-level ``litellm.acompletion`` / ``litellm.aembedding`` /
``litellm.aresponses`` — the single point every chat / embedding / responses egress
funnels through once ``LLMRouter`` runs with ``org_only_inference=True`` (empty
router → dispatch falls to module-level litellm). The exact ``kwargs`` (the request
body that would be JSON-serialized onto the wire) are recorded verbatim.

``RecordingVectorStore`` is the at-rest counterpart: a chromadb-shaped fake that
records the ``documents`` / ``metadatas`` an ingest path would persist, so a B5
at-rest test can prove nothing raw is stored without a live vector DB.

``assert_no_pii_egressed`` is the shared assertion: it scans every captured wire
for each corpus value's raw bytes. It cross-checks with ``independent_pii_scan`` —
an oracle built from regexes that are DELIBERATELY NOT the ``patterns.py`` set under
test — so a leak is never missed merely because the production regex that should
have caught it is the same one that failed.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable, List, Sequence


# ── Independent oracle — these regexes are intentionally a SEPARATE implementation
#    from gateway patterns.py, so leak detection does not depend on the same code
#    under test (mirrors tests/leakhunt/capture_addon.py). ──
_ORACLE = {
    "ssn": re.compile(r"\b\d{3}[- ]\d{2}[- ]\d{4}\b"),
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    "phone_sep": re.compile(r"(?<!\d)\d{3}[ .\-]\d{3}[ .\-]\d{4}(?!\d)"),
    "card": re.compile(r"\b(?:\d[ -]?){13,16}\b"),
    "openai_key": re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    "aws_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}\b"),
    "github": re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
}


def independent_pii_scan(text: str) -> List[str]:
    """Return the sorted oracle classes present in ``text`` (diagnostic signal)."""
    return sorted(k for k, rx in _ORACLE.items() if rx.search(text))


def serialize_wire(kwargs: dict) -> str:
    """The serialized request body litellm would put on the wire. ``messages`` /
    ``input`` / ``tools`` are forwarded verbatim and JSON-serialized into the HTTP
    body, so ``json.dumps(kwargs)`` is a faithful stand-in for the wire bytes.
    ``default=str`` ensures a stray non-JSON object still stringifies INTO the scan
    (a raw PII object would still be caught, never silently skipped)."""
    return json.dumps(kwargs, default=str)


class _StubResp:
    """Minimal litellm response — only ``.model_dump()`` is consumed by the router."""

    def __init__(self, payload: dict):
        self._payload = payload

    def model_dump(self) -> dict:
        return self._payload


class RecordingProvider:
    """Captures the exact bytes each upstream call receives.

    Usage::

        prov = RecordingProvider()
        prov.install(monkeypatch)
        await router.acompletion(body, redacted_content=red)
        prov.assert_no_pii_egressed(REDACTABLE)
    """

    def __init__(self) -> None:
        self.chat: List[dict] = []
        self.embed: List[dict] = []
        self.responses: List[dict] = []

    def install(self, monkeypatch) -> "RecordingProvider":
        import litellm

        async def _fake_acompletion(**kwargs):
            self.chat.append(kwargs)
            return _StubResp({
                "id": "chatcmpl-rec",
                "object": "chat.completion",
                "model": kwargs.get("model"),
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            })

        async def _fake_aembedding(**kwargs):
            self.embed.append(kwargs)
            return _StubResp({
                "object": "list",
                "model": kwargs.get("model"),
                "data": [{"object": "embedding", "index": 0, "embedding": [0.0, 0.1]}],
            })

        async def _fake_aresponses(**kwargs):
            self.responses.append(kwargs)
            return _StubResp({"id": "resp_rec", "object": "response", "output": []})

        monkeypatch.setattr(litellm, "acompletion", _fake_acompletion)
        monkeypatch.setattr(litellm, "aembedding", _fake_aembedding)
        monkeypatch.setattr(litellm, "aresponses", _fake_aresponses)
        return self

    @property
    def calls(self) -> List[dict]:
        """Every captured upstream call, regardless of surface."""
        return [*self.chat, *self.embed, *self.responses]

    @property
    def wires(self) -> List[str]:
        """Each captured call serialized to its wire bytes."""
        return [serialize_wire(k) for k in self.calls]

    @property
    def wire_blob(self) -> str:
        return "\n".join(self.wires)

    def assert_no_pii_egressed(self, corpus: Iterable[Any]) -> None:
        """Assert no raw value from ``corpus`` reached any captured wire."""
        assert_no_pii_egressed(self.wires, corpus)


def _raw_values(corpus: Iterable[Any]) -> List[str]:
    """Accept a list of PiiItem (``.raw``) or bare strings."""
    out: List[str] = []
    for item in corpus:
        raw = getattr(item, "raw", item)
        if isinstance(raw, str) and raw:
            out.append(raw)
    return out


def assert_no_pii_egressed(captures: Sequence[str], corpus: Iterable[Any]) -> None:
    """Core leak assertion: none of ``corpus``'s raw values appear in ``captures``.

    ``captures`` is a sequence of serialized wire strings (e.g. ``provider.wires``).
    ``corpus`` is a list of :class:`corpus.PiiItem` or raw strings.
    """
    blob = "\n".join(captures)
    leaked = sorted({raw for raw in _raw_values(corpus) if raw in blob})
    assert not leaked, (
        "raw PII reached the upstream wire (egress != verdict): "
        f"{leaked!r}\noracle classes on wire: {independent_pii_scan(blob)!r}\n"
        f"wire: {blob[:2000]!r}"
    )


class RecordingVectorStore:
    """chromadb-shaped fake recording exactly what an ingest path persists at rest.

    Records ``documents`` and ``metadatas`` so a B5 at-rest test can prove nothing
    raw is embedded/stored, without a live chromadb. ``query``/``get`` return the
    stored documents so a leak in metadata or content is inspectable.
    """

    def __init__(self) -> None:
        self.documents: List[str] = []
        self.metadatas: List[dict] = []
        self.ids: List[str] = []
        self.embeddings: List[Any] = []

    def add(self, ids=None, documents=None, metadatas=None, embeddings=None, **_):
        if ids:
            self.ids.extend(ids)
        if documents:
            self.documents.extend(documents)
        if metadatas:
            self.metadatas.extend(metadatas)
        if embeddings:
            self.embeddings.extend(embeddings)

    upsert = add  # pinecone-style alias

    @property
    def at_rest_blob(self) -> str:
        """Everything persisted (content + metadata) serialized for a leak scan."""
        return serialize_wire({"documents": self.documents, "metadatas": self.metadatas})

    def assert_no_pii_at_rest(self, corpus: Iterable[Any]) -> None:
        assert_no_pii_egressed([self.at_rest_blob], corpus)
