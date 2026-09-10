# Task 8b — design

## The change

```python
@functools.lru_cache(maxsize=64)
def _redact_all_cached(text: str) -> str:
    return _redact_obfuscated(text, _redact_all_raw(text))


def redact_all(text: str) -> str:
    return _redact_all_cached(text)
```

Same shape as task 1F's `canonicalize_for_detection` cache, which measured 1.4–1.7× on
`scan_output` with byte-identical output over every code point below 0x2000.

## The R4 reasoning, written down

A process-wide cache keyed on text is shared across tenants. It is safe here only because
`redact_all`'s result depends on **nothing but the text**: no org config, no policy set, no
request context. Two tenants sending identical bytes are entitled to identical output.

That is a real argument, not a hand-wave — but it is also exactly the kind of argument that
should be checked rather than trusted. `redact_all_scoped` takes `allowed_classes` and is
**not** cached here, precisely because its output depends on more than the text.

`maxsize=64` keeps the working set to roughly the in-flight requests, so the value comes
from the intra-request duplicate (`prompt` then `forwarded_prompt`) rather than from
long-lived cross-request retention.

## Verification

1. Corpus: `redact_all` byte-identical for all 748 items, cached vs uncached.
2. A test that `redact_all_scoped` is **not** routed through the cache — its output depends
   on `allowed_classes`, so caching on text alone would return another scope's answer.
3. A test that the same input twice returns the same object/value and only computes once.
4. Full suite.
5. E2E in **`full`** mode, `--repeat 2`: CPU per request and latency.
