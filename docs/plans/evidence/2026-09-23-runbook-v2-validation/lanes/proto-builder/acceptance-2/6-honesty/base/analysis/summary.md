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
- schedule drops (>5.0 ms): 0; lateness ms: n=240 p50=0.1206 p90=0.1451 p99=0.1627 p99.9=0.2255 max=0.2255 mean=0.1177
- join: {'joined': 238, 'by_nonce_fallback': 0, 'provider_records': 258, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=238 p50=84.9858 p90=88.6491 p99=102.4207 p99.9=107.8572 max=107.8572 mean=72.1754 |
| T_addon_first | n=238 p50=84.957 p90=88.6954 p99=102.4207 p99.9=107.7892 max=107.7892 mean=72.2288 |
| T_release_lag_max | n=238 p50=92.9619 p90=127.4964 p99=147.6327 p99.9=162.9382 max=162.9382 mean=97.4317 |
| T_release_lag_max_arrival | n=238 p50=90.6497 p90=108.525 p99=120.7547 p99.9=127.8589 max=127.8589 mean=86.469 |
| T_fw_addon | n=238 p50=92.9619 p90=127.4964 p99=147.6327 p99.9=162.9382 max=162.9382 mean=97.4317 |
| T_fw_addon_sse | n=167 p50=116.3488 p90=130.8683 p99=148.0852 p99.9=162.9382 max=162.9382 mean=109.286 |
| T_fw_addon_json | n=71 p50=84.5267 p90=88.4624 p99=102.4207 p99.9=102.4207 max=102.4207 mean=69.5492 |
| client_ttft_sse | n=167 p50=235.3238 p90=238.7846 p99=253.2195 p99.9=257.8748 max=257.8748 mean=223.4231 |
| provider_sched_err_last | n=238 p50=0.0511 p90=0.0932 p99=0.1002 p99.9=0.1016 max=0.1016 mean=0.0543 |
| provider_sched_err_max_per_stream | n=238 p50=0.0641 p90=0.1009 p99=0.1161 p99.9=0.8568 max=0.8568 mean=0.0671 |
| provider_write_max | n=238 p50=0.0474 p90=0.0672 p99=0.2024 p99.9=0.6989 max=0.6989 mean=0.0477 |

- release lag: 238 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/proto-builder/acceptance-2/6-honesty/base/olg', 'samples': 60, 'busy_max': 30.181350477466683, 'busy_mean': 13.68, 'machine': 'c4-standard-16', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 0, 'busy_max': None, 'busy_p95': None}
- completeness: [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/proto-builder/acceptance-2/6-honesty/base/olg', 'scheduled': 260, 'recorded': 260, 'interrupted': False}]
- health olg base: gc_cycles=0 sched_latency_max_ms=0.16384 tcp=None
