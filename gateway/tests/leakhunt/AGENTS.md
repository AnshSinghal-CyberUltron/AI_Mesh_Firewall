# gateway/tests/leakhunt — leak-hunt egress capture rig (A2)

This is the pytest gate for every leak (B-) story. Run it with the gateway venv:

```bash
cd gateway && ./.venv/bin/python -m pytest tests/leakhunt -q
```

## Why this dir lives OUTSIDE `ai_mesh_gateway/`
The CLAUDE.md gate path is literally `tests/leakhunt` (resolved from `gateway/`), so
the suite must sit at `gateway/tests/leakhunt/`. Because it is outside the package,
`ai_mesh_gateway/conftest.py` does NOT load — `conftest.py` here puts
`ai_mesh_gateway` on `sys.path` (so `import main` / `from llm_router import ...`
resolve like the in-tree tests) and adds this dir (so `import corpus` /
`import recording_provider` resolve as flat sibling modules). Do not add `__init__.py`.

The repo-root `tests/leakhunt/capture_addon.py` is a SEPARATE mitmproxy addon for
live-fleet capture — it is NOT part of this pytest suite.

## Files
- `corpus.py` — `PiiItem` + `CORPUS`, the single source of truth for raw PII values
  that must never egress. `.covered` = neutralized by today's redactor;
  `KNOWN_LEAKS` = real gaps the B-stories fix; `intentional=True` = kept raw BY
  DESIGN (e.g. bare 10-digit with no phone cue — ambiguous order-id shape).
- `recording_provider.py` — `RecordingProvider.install(monkeypatch)` patches
  `litellm.acompletion/aembedding/aresponses`, the egress choke point reached when
  `LLMRouter({"org_only_inference": True})` runs with an empty router. `wires` =
  captured request bytes. `assert_no_pii_egressed(corpus)` substring-scans them;
  `independent_pii_scan` is an oracle whose regexes are deliberately NOT `patterns.py`.
  `RecordingVectorStore` records at-rest documents+metadata for the B5 at-rest test.

## Conventions / gotchas
- Egress = truth: only assert on the captured **wire bytes**, never on the trace or
  the client response (those are pre-redaction / model output).
- Mark a documented-but-unfixed leak `@pytest.mark.xfail(strict=False)` so the gate
  stays green and the fix shows up as an XPASS when a B-story lands.
- The fail-closed digit backstop (`_redact_text_with_backstop`) and `redact_all`'s
  phone patterns are BOTH contiguous-digit only (`\d{7,}` / `\d{10}`), so any
  separator-split phone format (5+5 spaced) slips both — this is the G0 root cause.
- Add a new leaking format by appending a `PiiItem` to `corpus.py`; the existing
  `assert_no_pii_egressed` gate picks it up automatically.
