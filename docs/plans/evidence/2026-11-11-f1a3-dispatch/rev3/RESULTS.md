# F1a-3 plan revision 3: evidence

Backs revision 3 of `docs/plans/2026-11-11-f1a3-dispatch-credentials.md` (§3.3, §3.4, §3.6, §3.7, §4.2, §6.5). Prototypes, probes and a simulation only, no product code. CPU-sensitive runs took the shared heavy lock once (`bench/out/run_rev3.log`); the host (2 vCPU Xeon 2.10 GHz) was shared with other agents, and each run records the load and top CPU consumers (`bench/out/*.env.txt`).

**Credit.** `bench/hol_pacing_probe.py` is Plan Reviewer B's head-of-line probe (#121 rev 2, review 5871411489, `bench/hol_probe.py`) with the revision-3 read discipline added as a variant (marked "rev 3"). `sim/stream_slots_rev3_sim.py` follows Reviewer B's stream-slot model (same scenarios, seed, horizon and window) with decisions taken by the prototype policy; `sim/reviewer_b_model_rerun.jsonl` is B's model re-run unchanged (it reproduces B's 50.6% / 99.8% / 0% / 75.5%). `proto/check_stream_slots_proto.py` reuses Reviewer A's controller helper (review 5871181186) and turns A's door probe into a test on the revision-3 design. The keep-alive and NAT64 probes extend A's `test_keepalive_tick_by_wait_for_slices_kills_the_routed_stream` and `test_nat64_network_specific_prefix_passes_the_address_policy`.

## 1. Environment

- Python 3.12.3; private venv from revision 2 (`aiohttp==3.14.5` C parser, `uvloop==0.23.0`, `httpx==0.28.1`, `cryptography==50.0.2`); the prototype tests ran in the shared `venv-ci` (pytest). Nothing installed into shared venvs.
- gateway_v2 imported from this worktree (develop `562619c8` code, unchanged by this PR).

## 2. Stream-slot prototype (`proto/`; plan §3.6.1–§3.6.3)

```
cd /tmp && PYTHONPATH=<wt>/gateway_v2:<this dir>/proto python -m pytest -q -p no:cacheprovider <this dir>/proto/check_stream_slots_proto.py
PY=<python with pytest> GW=<wt>/gateway_v2 proto/mutation_check.sh
```

- `slots_core.py`: the pure policy (count and byte reservations, per-org and total wait bounds, waiters first, smallest dominant share on release, reserve, drain flush) with an `invariants()` check. `stream_slots_proto.py`: the asyncio wrapper (one acquire per request, admission hand-off that keeps the drain record, cancel-after-grant release, wait expiry → `upstream_inflight_limited`) and `HandoffAdmission`, built from develop's `complete` + `register_stream` + `finish_stream` plus the prune the plan asks F1a-1 to own.
- **13 tests pass** (`proto/out/tests.txt`), against develop's real `AdmissionController`:
  - drain with a live handed-off stream: waits **60.0 s**, declares **1** termination, request-queue depth **0**; after the stream ends: 0 s, 0; 1,000 sequential requests leave **0** drain records;
  - Reviewer A's door case (worker full, org A sends 15 more, org B one): A gets w waiters and 15 − w `wait_full` refusals, **B is admitted and served**, request-queue depth 0 (w = 1 and w = 2);
  - cancel after grant returns the slot to the next waiter; a fallback runs under the same slot (revision 2's per-candidate acquire ends attempt 2 with `wait_expired`);
  - idle worker: S − R to one org, all S with R = 0; release to the smallest dominant share even when another org's waiter is older; byte budget; wait bounds; drain flush;
  - a 10-trial × 1,000-step random property: capacity, holdings, wait bounds and work conservation after every step. It found one real rule gap while the prototype was written: cancelling an org's head waiter can expose a smaller waiter of the same org that fits, so `cancel()` must run the grant loop (now in the policy and in the plan).
- **Mutation check** (`proto/out/mutation.txt`): six mutants, each reverting one rule (revision 2's hand-off, the cancel-after-grant release, the cancel grant loop, the per-org wait cap, the reserve, smallest-share release); **each makes its test fail**.

## 3. Fairness simulation (`sim/`; plan §3.6.3)

```
python -I sim/stream_slots_rev3_sim.py > sim/stream_slots_rev3_sim.jsonl      # deterministic, seed 7, < 1 min CPU
python -I <B's stream_slots_sim.py> > sim/reviewer_b_model_rerun.jsonl         # B's model, unchanged
```

Modelled: Q = 14 admission slots held only for a 20 ms pipeline; hand-off releases the admission slot whatever follows; W = 14 waiters, w per org; 2 s wait; S = 470 (scenario `bytes`: S = 150, B = 203 MiB); counting window 10–290 s. Results and their reading: plan §3.6.3 (both tables). The loris attacker's S streams are opened over the first 5 s (B's model opened them at t = 0 with no pipeline time; with a 20 ms pipeline and 14 admission slots an instantaneous burst would mostly be shed at the door, which would flatter the design).

## 4. Read pacing (`bench/`; plan §3.6.4)

```
bench/run_rev3.sh      # one heavy-lock acquisition: part A (head-of-line) and part B (memory, §5)
```

- Part A ran in acquisition 1 (`bench/out/run_rev3.log`: queued 08:51:57Z, acquired 09:10:32Z, released 09:14:37Z; load 1.3–2.5 from other agents' unlocked work). Because one row of it did not fit its neighbours (100 µs, 1 unpaced stream, pacing: p99 64.9 ms against 4.6 ms with 4 unpaced streams), `bench/run_rev3_repeat.sh` repeated the pace and none variants in acquisition 2 (`bench/out/run_rev3_repeat.log`: queued 09:15:07Z, acquired 09:31:39Z, released 09:35:38Z; load 0.3–1.1; the top CPU consumers before every row are in `bench/out/hol_rev3_repeat.env.txt`, and the probe itself was the only busy process): 24 rows.
- Client: 64 paced streams (200 events at 20 ms ± 30%) plus 0/1/4 unpaced streams, on the plan §3.13 aiohttp session and the shipped decoder; per-event work 0 µs (dispatch scope) or 100 µs; declared `c_event` 31.9 µs or 131.9 µs gives caps of 362/346 Hz and 88/84 Hz per stream (E_max / streams). Lateness = decode time − the server's CLOCK_MONOTONIC send time (B's method).
- `python -I bench/summarize_hol.py bench/out` prints this table (median, range and n over both acquisitions):

| Per-event work | Unpaced streams | p99 lateness ms: none (B's client) | yield | pace (rev 3) | util none / pace | unpaced events/s per stream none / pace | CPU us per event none / pace |
|---|---|---|---|---|---|---|---|
| 0 us | 0 | 0.58 (n=1) | 0.85 (n=1) | 0.67 (0.66-1.37, n=3) | 0.18 / 0.17 | 0 / 0 | 58.6 / 55.6 |
| 0 us | 1 | 274.06 (n=1) | 4.69 (n=1) | 1.42 (0.63-2.43, n=3) | 0.34 / 0.18 | 25369 / 365 | 12.3 / 53.5 |
| 0 us | 4 | 295.3 (n=1) | 36.39 (n=1) | 1.27 (1.03-3.82, n=3) | 0.47 / 0.18 | 9980 / 336 | 11.3 / 47.6 |
| 100 us | 0 | 2.73 (1.72-3.37, n=4) | 3.47 (n=1) | 2.86 (1.81-3.48, n=4) | 0.46 / 0.47 | 0 / 0 | 152.1 / 157.6 |
| 100 us | 1 | 603.265 (590.58-756.31, n=4) | 37.74 (n=1) | 3.89 (2.72-64.88, n=4) | 0.98 / 0.46 | 6352 / 89 | 111.2 / 152.6 |
| 100 us | 4 | 2242.66 (2208.81-2856.78, n=4) | 129.72 (n=1) | 4.35 (1.71-5.75, n=4) | 0.95 / 0.47 | 1782 / 82 | 110.4 / 156.6 |

- Reading: B's client reproduces his numbers at 100 µs (594 ms and 2.4 s in his run); bounded reads plus a yield cut the damage but leave 36–130 ms; the token bucket holds each unpaced stream at its cap and keeps the paced streams within about 1–2 ms of their no-flood lateness, at the no-flood utilisation. The "CPU µs per event" column mixes paced and unpaced events, so only its no-flood rows measure the pacing cost: within noise at 0 µs, +5.5 µs (+3.6%) at 100 µs.

## 5. Per-stream memory bound (`probes/stream_memory_bound.py`; plan §3.6.2)

```
probes/stream_memory_bound.py server <port>
probes/stream_memory_bound.py hostile <port> <U|E|S> <read_bufsize> 64 262144 <SO_RCVBUF or 0>   (run_rev3.sh part B)
probes/stream_memory_bound.py retained
```

Hostile per-stream bytes (`probes/out/hostile.jsonl`; 64 streams, 256 KiB event bound, `SO_RCVBUF` 64 KiB unless stated; RSS peak minus the idle-connection baseline, ÷ 64):

| Shape | `read_bufsize` 16 KiB | 64 KiB | Client kernel receive queue per stream |
|---|---|---|---|
| U: unterminated event just under the bound, then stall | 437 KiB | 452 KiB | 0 |
| E: back-to-back events just under the bound, through the pacer (⌈bytes/1 KiB⌉ tokens) | **498 KiB** | 556 KiB | 0 |
| S: reader stopped, upstream writing | 121 KiB | 235 KiB | **96 KiB** (effective `SO_RCVBUF` 128 KiB) |
| S, `SO_RCVBUF` autotuned (this host's `tcp_rmem` max is 32 MiB) | 116 KiB | — | **495 KiB** after 3 s (rcvbuf grew to 589 KiB) |

The system-wide TCP memory column in the JSON counts both ends on this host (the upstream's send buffers dominate it) and is not used. A smoke run at 8 streams gave higher per-stream figures (up to 1,032 KiB for E) because the allocator's fixed overhead is divided by fewer streams; the plan uses the 64-stream figures, and the CI job measures on the image.

Retained request (`probes/out/retained.jsonl`; tracemalloc; bodies of short 1 KiB messages and long messages, at 64 KiB and at `max_request_bytes()` for `per_worker_rss` 400 MiB (1 MiB) and 1 GiB (2.56 MiB)): raw + `CanonicalRequest` hold 2.0–2.5× (2.0–2.3× at the maximum body); plus one rendered upstream body **3.0–3.5×**; transient peak during the render **4.2–4.8×**. Plan §3.6.2 charges 4× the actual body to a held stream and 2.3× the maximum body to each pipeline or waiting request.

## 6. Small probes (`probes/`)

| File | What it shows | Plan |
|---|---|---|
| `nat64_nsp_decode.{py}`, `out/nat64_nsp_decode.jsonl` | RFC 6052 decode for NSP lengths /32, /40, /48, /56, /64, /96 (u-octet skipped): all 24 synthesised private, link-local, loopback and CGNAT embeddings are `is_global` and pass when the prefix is undeclared; with it declared, all 30 verdicts are right; the well-known prefix needs no declaration (exit 0) | §4.2 |
| `keepalive_owner_task.py`, `out/keepalive_owner_task.jsonl` | On develop's `bounded_events`: one task owning the pending `__anext__` ticks 2 keep-alives during a 0.25 s pause and still receives the event; closing the iterator while that read is pending raises `aclose(): asynchronous generator is already running`; cancel → await → `aclose` closes cleanly (exit 0) | §3.7 |
