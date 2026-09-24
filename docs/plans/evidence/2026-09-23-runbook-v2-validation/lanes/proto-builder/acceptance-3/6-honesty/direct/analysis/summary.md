# Harness analysis (direct, policy=enforce) — PASS

| check | result |
|---|---|
| zero_schedule_drops | PASS |
| zero_errors | PASS |
| provider_p99_sched_err_within_tol | PASS |
| loadgen_cpu_max_lt_limit | PASS |
| run_valid | PASS |
| all_joined | PASS |

- offered: 240 over 60 s = 4.0 req/s (configured 4.0)
- qualified: 240 (4.0 req/s); expected blocks: 0; errors: 0 (rate 0.0)
- error reasons: {}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=240 p50=0.1087 p90=0.1226 p99=0.1414 p99.9=0.1561 max=0.1561 mean=0.1077
- join: {'joined': 240, 'by_nonce_fallback': 0, 'provider_records': 260, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=240 p50=0.1436 p90=0.2045 p99=0.2565 p99.9=0.2694 max=0.2694 mean=0.1542 |
| T_addon_first | n=240 p50=0.159 p90=0.2045 p99=0.2644 p99.9=0.2894 max=0.2894 mean=0.1635 |
| T_release_lag_max | n=240 p50=0.1787 p90=0.2089 p99=0.2974 p99.9=0.305 max=0.305 mean=0.1824 |
| T_release_lag_max_arrival | n=240 p50=0.1787 p90=0.2089 p99=0.2974 p99.9=0.305 max=0.305 mean=0.1824 |
| T_fw_addon | n=240 p50=0.1787 p90=0.2089 p99=0.2974 p99.9=0.305 max=0.305 mean=0.1824 |
| T_fw_addon_sse | n=168 p50=0.1717 p90=0.201 p99=0.2992 p99.9=0.305 max=0.305 mean=0.1774 |
| T_fw_addon_json | n=72 p50=0.1884 p90=0.2177 p99=0.2644 p99.9=0.2644 max=0.2644 mean=0.1939 |
| client_ttft_sse | n=168 p50=150.222 p90=150.2508 p99=150.3225 p99.9=150.359 max=150.359 mean=150.2214 |
| provider_sched_err_last | n=240 p50=0.0741 p90=0.0987 p99=0.1877 p99.9=0.3076 max=0.3076 mean=0.0717 |
| provider_sched_err_max_per_stream | n=240 p50=0.0941 p90=0.2355 p99=0.3028 p99.9=0.3195 max=0.3195 mean=0.1194 |
| provider_write_max | n=240 p50=0.0289 p90=0.0414 p99=0.0594 p99.9=0.1153 max=0.1153 mean=0.028 |

- release lag: 240 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/proto-builder/acceptance-3/6-honesty/direct/olg', 'samples': 60, 'busy_max': 20.47037096300164, 'busy_mean': 8.18, 'machine': 'c4-standard-16', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 0, 'busy_max': None, 'busy_p95': None}
- completeness: [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/proto-builder/acceptance-3/6-honesty/direct/olg', 'scheduled': 260, 'recorded': 260, 'interrupted': False}]
- health olg direct: gc_cycles=0 sched_latency_max_ms=0.098304 tcp=None
