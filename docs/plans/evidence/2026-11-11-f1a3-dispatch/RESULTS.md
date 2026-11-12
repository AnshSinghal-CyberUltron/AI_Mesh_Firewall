# F1a-3 dispatch and credentials: evidence

Backs `docs/plans/2026-11-11-f1a3-dispatch-credentials.md`. Everything here is a prototype or a probe, not product code. Every number is CPU time (thread or process), never wall time, because the host (2 CPUs, about 10 agents) was shared. The load average is recorded with each run.

## 1. Environment

- **Interpreters.**
  - Production is Python 3.12, matching the image (`gateway_v2/Dockerfile`, `python:3.12-slim`).
  - The probes use a private venv at `/usr/bin/python3.12` with `cryptography==46.0.3`, `httpx==0.28.1`, `uvloop==0.21.0`, `aiohttp==3.12.15`, `h2==4.3.0`, `redis==6.4.0` and `psycopg[binary]==3.2.10`. Nothing was installed into the shared venvs.
  - `ssrf_probe_py313.json` and `probe_dispatch.txt` (contract half) also use the shared `venv-ci` (Python 3.13).
- **Library:** OpenSSL 3.0.13. Linux 6.18, x86_64.
- **Code under test:** gateway_v2 at `develop` `25c33c86`, imported from this worktree.

## 2. Credential envelope cost (`aead_bench.py` → `aead_results.json`)

```
python -I aead_bench.py --iters 20000 --repeats 7
```

Thread CPU ns per operation, the minimum over 7 repeats of the mean of 20,000 operations. The committed file is the second run, which added the Fernet and HMAC rows. It ran unlocked: about 25 s of CPU, below the 2-minute threshold, at load average 5.9–7.2. The first run, under the lock at load 1.8, agreed within about 10%:

| Operation (39 / 164 / 256-byte key) | first run, locked | committed run |
|---|---|---|
| `dec_hot`: credential decrypt, cached `AESGCM(DEK)` | 520 / 580 / 583 | 586 / 545 / 818 |
| `dec_new`: `AESGCM(DEK)` built per call, then decrypt | 1,459 / 1,743 / 1,970 | 1,840 / 1,605 / 1,988 |
| `cold`: KEK unwrap + build + decrypt | 1,989 / 2,150 / 2,205 | 2,445 / 2,106 / 2,325 |
| `aad`: AAD encode, 143 bytes | 1,680 | 1,651 |
| `b64`: decode of `n` + `ct` | 509 / 798 / 1,069 | 486 / 945 / 1,126 |
| `header`: `b"Bearer " + key` | 62 / 61 / 60 | 61 / 56 / 59 |
| `enc`: encrypt with a fresh nonce (projector) | 1,076 / 1,063 / 1,005 | 963 / 1,032 / 1,067 |
| `fernet`: decrypt of control's token (projector) | — | 11,786 / 14,640 / 13,420 |
| `hmac`: projector memo digest `s` | — | 1,702 / 1,958 / 2,066 |
| `AESGCM` object, RSS per cached entry | 2,424 B | 2,424 B |

Also checked:
- **AEAD properties.** A round trip decrypts. The same ciphertext under a changed org, base URL or version raises `InvalidTag` (`aead_bench.py:125-137`, asserted on every run).
- **Memory.** A bare 32-byte DEK costs 90 B of RSS per entry; an `AESGCM` object costs 2,433 B. This was checked separately with current RSS from `/proc/self/statm` over 100,000 objects.

## 3. Current-behaviour probes (develop `25c33c86`)

| File | What it shows |
|---|---|
| `probe_trust_env.txt` | The shipped `httpx_client_factory`, with `HTTPS_PROXY` set: `trust_env True`, the `https://` mount's pool is `AsyncHTTPProxy`, and a default `authorization` header is set (plan C3) |
| `probe_dispatch.txt` | `PinnedRefused` is neither `httpx.HTTPError` nor `OSError`, so the provider's `except` clauses miss it (C6). The contract on this host: `workers 1`, `pool_size(PROVIDER) 1`; upstream first event 30 s, gap 0.8 s, total 299.94 s; edge inter-chunk 1.0 s (C4, C13) |
| `probe_pool.txt` (`probe_pool.py`) | The dev provider, built exactly as `dev_profile.py:302-311` does, with 4 concurrent streams and a 1 s upstream hold: complete at **1.35 / 2.36 / 3.37 / 4.38 s**. The streams are serialised on one connection (C4) |
| `ssrf_probe_py312.json`, `ssrf_probe_py313.json` (`ssrf_probe.py`) | The shipped `resolve_checked` with the default empty allowlist: `100.64.0.1` **allowed** (not `is_global`); transition-prefix IPv6 refused only through `::/8` "reserved" or `2002::/16` "private" (C5). The shipped `SSEDecoder`: CRLF framing gives 0 frames and is not finished; a `: ping` comment sets malformed (C8). Identical on 3.12 and 3.13 |
| `probe_pickle.txt` | The `vector_credentials.OrgCredential` precedent still pickles on 3.12 with the secret in the bytes. Deleting the dataclass hooks is not enough. An existing GW30 issue, reported in the plan §2.7 |

## 4. Dispatch client benchmark (`client_bench.py`, `upstream.py`, `run_bench.sh` → `client_results.json`)

> **Superseded for scale claims (revision 2, 12 Nov).** `upstream.py` writes each whole response in one `transport.write`, so aiohttp read each stream in **1** read while httpx parsed 23 events (both plan reviews). These numbers measure a burst, not a model stream. The plan's §6 now rests on paced streams: `rev2/RESULTS.md`.

```
BENCH_LOCK=env run_bench.sh <python> client_results.json --requests 1500 --warmup 200 --repeats 2 --concurrency 32 --memory-connections 256
for c in 1 8 128; do BENCH_LOCK=env run_bench.sh <python> conc_$c.json --only httpx_raw,aiohttp_raw,design --requests 800 --warmup 100 --repeats 2 --concurrency $c; done   # -> client_concurrency.json
```

`run_bench.sh` does the following:
- It starts two `upstream.py` processes (plain and TLS 1.3 with a throwaway self-signed certificate) on `172.17.0.1`, a private bridge address. The SSRF check refuses loopback, and the design path must pass it, with an allowlist entry.
- Each response is 20 content chunks, a finish chunk, a usage chunk and `[DONE]`, chunked, one HTTP chunk per SSE event.
- It runs the client under `flock /home/claude/work/.heavy.lock nice -n 10` by default. `BENCH_LOCK=env` skips the lock, which was used here (see the run note below).
- It removes the proxy variables, and uses a fresh random bearer per run.
- CPU is the client process's `process_time` (all threads) per request. The median of the runs is reported, after the warm-up.

**Run note.**
- The full-size run (6,000 × 3 per variant) queued on the shared heavy lock for 52 minutes behind more than 15 jobs and was withdrawn.
- The reduced run above took **70 s of CPU in total** (bash `time`: user 66.4 s, sys 4.2 s; wall 155 s). That is under the 2-minute threshold for which the guide requires the lock, so it ran unlocked at `nice -n 10`.
- Load average: 3.4 at the start, 5.2 at the end.
- The plain upstream counted 25,856 authorised requests and 0 unauthorised (the bearer check passed for every request). Every variant reported 0 errors.

Client CPU µs per request (`client_results.json`, concurrency 32, 1,500 requests × 2 runs):

| Variant | median | runs | upstream CPU µs/req | errors |
|---|---|---|---|---|
| `current` (shipped `OpenAIUpstreamProvider`, httpx, `SSEDecoder`) | 3,190 | 3,267 / 3,112 | 117 | 0 |
| `design` (httpx, `trust_env=False`, `PinnedTransport`, per-request decrypt, `SSEDecoder`) | 3,155 | 3,213 / 3,098 | 127 | 0 |
| `design_json` | 2,637 | 2,530 / 2,744 | 127 | 0 |
| `httpx_raw` | 2,670 | 2,843 / 2,497 | 110 | 0 |
| `httpx_json` | 2,334 | 2,355 / 2,314 | 110 | 0 |
| `aiohttp_raw` | 204 | 213 / 194 | 23 | 0 |
| `aiohttp_decode` | 361 | 354 / 369 | 27 | 0 |
| `aiohttp_json` | 135 | 143 / 127 | 20 | 0 |
| `design_tls` (TLS 1.3, pooled) | 3,289 | 3,529 / 3,049 | 183 | 0 |
| `tls_handshake` (TLS, keep-alive off; 500 requests per run) | 3,347 | 3,186 / 3,508 | 730 | 0 |

- `getaddrinfo("localhost")` on the loop: **68.3 µs** of CPU per call.
- Pooled connection RSS (httpx, 256 connections opened concurrently, then idle): plain **22,240 B**, TLS **36,416 B** per connection.

Concurrency sweep (`client_concurrency.json`, 800 × 2; three separate unlocked runs, each under 30 s of CPU, load 4.9–5.6), µs per streamed request:

| In flight | `design` | `httpx_raw` | `aiohttp_raw` |
|---|---|---|---|
| 1 | 2,008 | 1,491 | 137 |
| 8 | 1,922 | 1,587 | 212 |
| 32 | 3,155 | 2,670 | 204 |
| 128 | 5,002 | 5,321 | 150 |

Reading:
- httpx costs about 1.5–2 ms per request even with one in flight, and grows with concurrency. aiohttp stays flat at about 0.14–0.21 ms.
- The real `SSEDecoder` adds about 160 µs per 23-event stream (`aiohttp_decode` − `aiohttp_raw`).
- A TLS handshake adds about 550 µs of CPU on the upstream side. On the client the difference stayed inside httpx's noise.

## 5. Provider documentation checked (11 Nov 2026)

- Google Gemini OpenAI compatibility: <https://ai.google.dev/gemini-api/docs/openai>. Base URL `…/v1beta/openai/`; Bearer auth; `reasoning_effort` low/medium/high (`none` on 2.5 only); embeddings at `…/openai/embeddings`. `logit_bias`, `seed`, the penalties and the `developer` role are not mentioned, hence "(capture)" in the plan.
- Mistral chat completions: <https://docs.mistral.ai/api/endpoint/chat>. `random_seed` (no `seed`), `max_tokens` only, no `stream_options` and no `logit_bias`; roles system, user, assistant and tool (no `developer`); `reasoning_effort` none…xhigh.
- Cohere Compatibility API: <https://docs.cohere.com/docs/compatibility-api>. Base `https://api.cohere.ai/compatibility/v1`; `developer` role supported; `reasoning_effort` only `none`/`high`; `logit_bias` and `n` unsupported; embeddings supported (`encoding_format` float or base64).
- Meta Llama API: <https://fuma-docs.api7.ai/ai-gateway/providers/meta-llama-api>. The OpenAI-compatible base is `https://api.llama.com/compat/v1/` (not `/v1`); Bearer auth.
- DeepSeek: <https://api-docs.deepseek.com/guides/reasoning_model>. The page has changed (v4 model names, a `thinking` parameter). Reasoning parameter behaviour is therefore "(capture)" in the plan.

## 6. Reproduce

```bash
python3.12 -m venv /tmp/f1a3 && /tmp/f1a3/bin/pip install cryptography==46.0.3 httpx==0.28.1 \
  uvloop==0.21.0 aiohttp==3.12.15 h2==4.3.0 redis==6.4.0 'psycopg[binary]==3.2.10'
cd docs/plans/evidence/2026-11-11-f1a3-dispatch
/tmp/f1a3/bin/python -I aead_bench.py
/tmp/f1a3/bin/python -I ssrf_probe.py
./run_bench.sh /tmp/f1a3/bin/python client_results.json --requests 1500 --warmup 200 --repeats 2   # add BENCH_LOCK=env to skip the shared lock
```
