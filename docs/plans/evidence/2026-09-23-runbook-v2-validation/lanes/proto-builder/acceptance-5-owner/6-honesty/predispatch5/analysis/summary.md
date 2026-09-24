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
- schedule drops (>5.0 ms): 0; lateness ms: n=240 p50=0.1112 p90=0.1278 p99=0.2423 p99.9=0.2608 max=0.2608 mean=0.114
- join: {'joined': 238, 'by_nonce_fallback': 0, 'provider_records': 258, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=238 p50=90.7581 p90=93.4449 p99=110.8741 p99.9=131.2689 max=131.2689 mean=77.4075 |
| T_addon_first | n=238 p50=90.709 p90=93.4449 p99=110.9 p99.9=131.2305 max=131.2305 mean=77.3655 |
| T_release_lag_max | n=238 p50=102.1608 p90=132.9381 p99=149.188 p99.9=151.3642 max=151.3642 mean=102.393 |
| T_release_lag_max_arrival | n=238 p50=93.2889 p90=114.1612 p99=129.4288 p99.9=151.2699 max=151.2699 mean=91.7883 |
| T_fw_addon | n=238 p50=102.1608 p90=132.9381 p99=149.188 p99.9=151.3642 max=151.3642 mean=102.393 |
| T_fw_addon_sse | n=167 p50=128.5154 p90=133.4396 p99=151.2699 p99.9=151.3642 max=151.3642 mean=113.9715 |
| T_fw_addon_json | n=71 p50=90.6274 p90=93.2774 p99=124.1902 p99.9=124.1902 max=124.1902 mean=75.159 |
| client_ttft_sse | n=167 p50=240.7729 p90=243.6258 p99=260.9251 p99.9=281.2652 max=281.2652 mean=228.3553 |
| provider_sched_err_last | n=238 p50=0.0496 p90=0.0899 p99=0.1027 p99.9=0.1052 max=0.1052 mean=0.0514 |
| provider_sched_err_max_per_stream | n=238 p50=0.063 p90=0.0992 p99=0.1272 p99.9=0.2429 max=0.2429 mean=0.0626 |
| provider_write_max | n=238 p50=0.0478 p90=0.0634 p99=0.2046 p99.9=0.2345 max=0.2345 mean=0.0448 |

- release lag: 238 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/proto-builder/acceptance-5-owner/6-honesty/predispatch5/olg', 'samples': 60, 'busy_max': 23.43814590924055, 'busy_mean': 13.53, 'machine': 'c4-standard-16', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 0, 'busy_max': None, 'busy_p95': None}
- completeness: [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/proto-builder/acceptance-5-owner/6-honesty/predispatch5/olg', 'scheduled': 260, 'recorded': 260, 'interrupted': False}]
- health olg predispatch5: gc_cycles=0 sched_latency_max_ms=0.08192 tcp=None
