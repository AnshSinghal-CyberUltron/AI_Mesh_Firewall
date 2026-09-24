# Harness analysis (sut, policy=enforce) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| error_rate_le_budget | FAIL |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 240 over 60 s = 4.0 req/s (configured 4.0)
- qualified: 238 (3.97 req/s); expected blocks: 0; errors: 2 (rate 0.008333333333333333)
- error reasons: {'http_403': 2, 'incomplete': 2, 'unjoined': 2, 'disposition_BLOCK': 2, 'stage_dispatch_S': 2, 'stage_out_S': 2}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=240 p50=0.1113 p90=0.127 p99=0.1567 p99.9=0.1839 max=0.1839 mean=0.1135
- join: {'joined': 238, 'by_nonce_fallback': 0, 'provider_records': 258, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=238 p50=90.8399 p90=93.6293 p99=109.6834 p99.9=112.7338 max=112.7338 mean=77.1722 |
| T_addon_first | n=238 p50=90.8399 p90=93.8851 p99=112.17 p99.9=113.2735 max=113.2735 mean=77.7165 |
| T_release_lag_max | n=238 p50=93.9973 p90=132.6336 p99=144.9378 p99.9=151.64 max=151.64 mean=101.8369 |
| T_release_lag_max_arrival | n=238 p50=93.441 p90=114.0141 p99=121.9721 p99.9=135.0287 max=135.0287 mean=91.576 |
| T_fw_addon | n=238 p50=93.9973 p90=132.6336 p99=144.9378 p99.9=151.64 max=151.64 mean=101.8369 |
| T_fw_addon_sse | n=167 p50=128.9195 p90=133.3033 p99=150.5341 p99.9=151.64 max=151.64 mean=113.4652 |
| T_fw_addon_json | n=71 p50=89.9 p90=93.4185 p99=110.5419 p99.9=110.5419 max=110.5419 mean=74.4859 |
| client_ttft_sse | n=167 p50=241.1219 p90=244.6875 p99=262.7891 p99.9=263.3312 max=263.3312 mean=229.1435 |
| provider_sched_err_last | n=238 p50=0.0514 p90=0.0931 p99=0.1015 p99.9=0.1029 max=0.1029 mean=0.0532 |
| provider_sched_err_max_per_stream | n=238 p50=0.0621 p90=0.1005 p99=0.1231 p99.9=2.6666 max=2.6666 mean=0.0734 |
| provider_write_max | n=238 p50=0.0485 p90=0.0661 p99=0.1899 p99.9=0.2377 max=0.2377 mean=0.0453 |

- release lag: 238 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/proto-builder/acceptance-3/6-honesty/predispatch5/olg', 'samples': 60, 'busy_max': 26.47952313633455, 'busy_mean': 13.42, 'machine': 'c4-standard-16', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 0, 'busy_max': None, 'busy_p95': None}
- completeness: [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/proto-builder/acceptance-3/6-honesty/predispatch5/olg', 'scheduled': 260, 'recorded': 260, 'interrupted': False}]
- health olg predispatch5: gc_cycles=0 sched_latency_max_ms=0.08192 tcp=None
