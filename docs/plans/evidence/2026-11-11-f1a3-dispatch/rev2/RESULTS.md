# F1a-3 plan revision 2: evidence

Backs revision 2 of `docs/plans/2026-11-11-f1a3-dispatch-credentials.md` (§2.2, §3.9, §3.13, §4.2, §6). Prototypes and probes only, no product code. CPU figures are thread or process CPU, never wall time; the host (2 vCPU, Intel Xeon 2.10 GHz, ~20 agents) was shared, and each run records the load and the top CPU consumers in its `*.env.txt`.

**Credit.** The paced benchmark reuses Plan Reviewer B's harness (`bench/sse_upstream.py`, `bench/client_cost.py`, review 5869723316) with one added variant, and Plan Reviewer A's lock-step harness unchanged (`bench/paced_bench_reviewer_a.py`, review 5869433944). `probes/from_reviewer_a/` and `probes/from_reviewer_b/` are the reviewers' own probes and outputs, copied with provenance headers (A's `test_repro_develop.py` is renamed `repro_develop_reviewer_a.py` so no pytest collector picks it up).

## 1. Environment

- Python 3.12.3 (the image's minor version), private venv: `aiohttp==3.14.5` (C parser), `httpx==0.28.1`, `httpcore==1.0.9`, `h11==0.16.0`, `uvloop==0.23.0`, `cryptography==50.0.2`, `redis==8.1.0`. Nothing installed into the shared venvs.
- gateway_v2 imported from this worktree (develop `25c33c86` code; identical to `562619c8` outside `edge/scan_calibration.py` and tests).
- The dev-profile full path (§4) runs the shared `venv-ci` (Python 3.12.3, the serve extras) for the gateway process.

## 2. Paced dispatch benchmark (`bench/`; plan §6.2–§6.4)

```
VENV=<py3.12 venv> GW=<worktree>/gateway_v2 bench/run_rev2.sh      # one heavy-lock acquisition
python -I summarize.py                                              # prints every table of plan §6
```

- `bench/run_rev2.sh` starts Reviewer B's `sse_upstream.py` per scenario (own process; ~325 B OpenAI 2025-shape chunks; chunked, one HTTP chunk per event; 20 ms ± 30% jitter), runs `client_cost.py` under `nice 10`, snapshots load and the top CPU consumers before and after (`out/<label>.env.txt`), and reads the upstream's authorised/unauthorised counters. Fresh random bearer per run; proxy variables unset.
- Scenarios: P1/P2/P3 = 24/200/600 events at 64 in flight; P5/P4 = 24/200 events at 256 in flight. Requests per run 512/256/128/1024/512, warm-up 64/64/64/256/256, 2 runs each.
- Run: queued 06:33Z, heavy lock acquired 07:09:23Z, released 07:24:21Z (`out/run_rev2.log`); load average 0.3–2.6. **0 errors in all 26 variant rows; 28,416 authorised upstream requests, 0 unauthorised.**
- `aiohttp_design` is the only addition to Reviewer B's client: the plan §3.13 transport (pinned `AbstractResolver` for a hostname URL, `socket_factory` dial guard, `trust_env=False`, `auto_decompress=False` + `Accept-Encoding: identity` + a refusal of any other `Content-Encoding`, `max_line_size`/`max_field_size` 8190, `allow_redirects=False`, a media-type check) plus the shipped `SSEDecoder`.
- Reconciliation run: Reviewer A's `paced_bench_reviewer_a.py`, unchanged, gap 0 and 5 ms, 32 in flight, 600 requests × 2 per client (`out/A_reconcile.txt`).
- Results and their reading: plan §6.3 (tables), §6.4 (cost at the planning distribution and the cost basis).

## 3. Full path on develop (`devpath/`; plan §6.4)

```
devpath/run_all.sh     # one heavy-lock acquisition; uses venv-ci for the gateway, the bench venv for the rest
```

- `devpath/run.sh <label> <events> <gap_ms> <requests> <concurrency>` boots develop's gateway_v2 with `AMF_DEV_PROFILE=1` (the example plan `gateway_v2/dev/plan.example.json`, `AMF_DEV_BUDGET=unlimited`, `AMF_DEV_PROVIDER_URL` → the paced upstream, one worker), warms it, measures 10 s of idle CPU, then drives `load_client.py` (own process) and reads the gateway process's `utime + stime` from `/proc`. CPU per qualified request = (busy − idle rate × wall) / qualified requests, where qualified = HTTP 200, content present, `[DONE]` last, no error frame.
- C4 (one upstream connection per worker) serialises the dev provider, so streams run one at a time. `run_all.sh` therefore also measures `current` and `aiohttp_design` with `client_cost.py` at **1 in flight** on the same upstream, so the pipeline share is a like-for-like subtraction.
- Not representative of production in these ways (plan §6.4 states them): dev identity and audit sinks, the example plan's detectors, no guard model (GPU tier), single-stream batching, httpx dispatch.

Results (`devpath/out/*.result.json`, `C1_e*.json`; heavy lock 07:30:52–07:39:58Z for D1, D2 and C1, 07:45:07–07:50:07Z for D2b and D3; load 0.8–3.5):

| Run | E | Qualified / sent | Gateway CPU per qualified request | Per event | Dispatch alone at 1 in flight: `current` / `aiohttp_design` |
|---|---|---|---|---|---|
| D1 | 24 | 96 / 96 | 27.2 ms | 1.13 ms | 8.0 / 4.0 ms |
| D2 | 200 | 24 / 24 | 470 ms as computed; **647 ms** with D1's idle rate (its idle window overlapped the gateway's boot: idle 0.044 CPU-s/s against 0.001) | — | 63.3 / 30.0 ms |
| D2b | 200 | 24 / 24 | **640 ms** (clean idle baseline 0.001) | 3.20 ms | (as D2) |
| D3 | 600 | 6 / 8 (two HTTP 504: C4 queues four requests on one connection, past the first-event bound) | **≈ 4.93 s** (all gateway CPU over the 6 qualified) | 8.22 ms | ≈ 189 ms (linear estimate) |

Fit through the three clean points: 4.1 + 0.66·E + 0.0126·E² ms (log-log slope ≈ 1.6). Pipeline share (full − `current`): 70% (E 24), 90% (E 200), 96% (E 600).

**Why** (`devpath/profile_e200.txt`, from `devpath/profile_run.sh`: cProfile of four 200-event streams): the gateway_v2 frames are dominated by `egress/stream.py:_process_delta` → `edge/dev_components.py:findings_on` → `detect/deterministic/canonicalize.py:canonicalize` (2,853 calls over 1.16 M characters). `egress/stream.py:547-548` runs the output detector over `text.whole`, the entire accumulated output, on every delta, so the work per stream is quadratic in events. The chat path wires that detector at `edge/chat_stream.py:450`.

## 4. Probes (`probes/`)

| File | What it shows | Plan |
|---|---|---|
| `aiohttp_transport_guard.{py,txt}` | aiohttp 3.14.5: without a dial guard an IP-literal URL connects and the resolver is never called (Reviewer B's B4, reproduced); with the `socket_factory` guard the literal and a rebinding answer each open **0** TCP connections; a hostname resolves through the pinned resolver (called once per connection, so aiohttp's DNS cache holds nothing unchecked); a 302 to `169.254.169.254` is not followed; a 64 MiB gzip body is refused from the head with 0 KiB RSS growth; the wire carries `Accept-Encoding: identity`; `HTTP(S)_PROXY` is ignored; the guard's exception is `ClientConnectorError.os_error`, so it maps to its own code | §3.9, §3.13, §4.2 |
| `osv_query.py`, `osv_2026-11-12.jsonl` | OSV advisories per version on 12 Nov 2026: aiohttp 3.12.15 72, 3.13.3 56, 3.13.5 36, 3.14.4 0, 3.14.5 0; cryptography 46.0.3 13, 50.0.2 0; httpx, httpcore, h11, uvloop 0 | §2.2, §3.13 |
| `stream_memory.{py,txt}` | Client RSS per in-flight paced stream (plain HTTP, decoder included): aiohttp 14.8 KB, httpx 25.9 KB | §3.6.2, §6.7 |
| `aead_cryptography_50.0.2.txt` | `aead_bench.py` on the new pin (5k × 3, unlocked): `dec_hot` 659–664 ns, `cold` 2.4–4.3 µs, AAD 1.87 µs, Fernet 8.9–10.0 µs | §2.2, §2.12 |
| `from_reviewer_a/` | Reviewer A's 20 develop reproductions (C3–C13, R17, lens 2 and 5) with its output, and the non-ASCII key probe (live C6 class) | §1 |
| `from_reviewer_b/` | Reviewer B's probes: admission concurrency 14–15 (C19), decompression amplification (C21), aiohttp resolver bypass (B4), view memory 240/341 MiB (B-S4) | §1, §2.6, §3.13 |

## 5. Reproduce

All commands run from this folder with a Python 3.12 venv holding the versions of §1 and `PYTHONPATH=<worktree>/gateway_v2`. Anything over about 2 minutes of CPU takes `/home/claude/work/.heavy.lock` (the run scripts do). The same scenarios are specified as a CI job for GitHub runners in plan §6.8.
