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
- schedule drops (>5.0 ms): 0; lateness ms: n=240 p50=0.121 p90=0.1506 p99=0.2549 p99.9=0.5059 max=0.5059 mean=0.1298
- join: {'joined': 240, 'by_nonce_fallback': 0, 'provider_records': 260, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=240 p50=0.1409 p90=0.1968 p99=0.2924 p99.9=0.3368 max=0.3368 mean=0.1535 |
| T_addon_first | n=240 p50=0.1558 p90=0.1975 p99=0.2924 p99.9=0.3368 max=0.3368 mean=0.1637 |
| T_release_lag_max | n=240 p50=0.1788 p90=0.2316 p99=0.3081 p99.9=0.3368 max=0.3368 mean=0.1875 |
| T_release_lag_max_arrival | n=240 p50=0.1788 p90=0.2316 p99=0.3081 p99.9=0.3368 max=0.3368 mean=0.1875 |
| T_fw_addon | n=240 p50=0.1788 p90=0.2316 p99=0.3081 p99.9=0.3368 max=0.3368 mean=0.1875 |
| T_fw_addon_sse | n=168 p50=0.1744 p90=0.2569 p99=0.3081 p99.9=0.3154 max=0.3154 mean=0.1841 |
| T_fw_addon_json | n=72 p50=0.1883 p90=0.2274 p99=0.3368 p99.9=0.3368 max=0.3368 mean=0.1954 |
| client_ttft_sse | n=168 p50=150.2075 p90=150.2375 p99=150.3791 p99.9=150.4496 max=150.4496 mean=150.2127 |
| provider_sched_err_last | n=240 p50=0.0609 p90=0.0894 p99=0.1734 p99.9=0.1968 max=0.1968 mean=0.0606 |
| provider_sched_err_max_per_stream | n=240 p50=0.1241 p90=0.2601 p99=0.3882 p99.9=0.4737 max=0.4737 mean=0.147 |
| provider_write_max | n=240 p50=0.0302 p90=0.0415 p99=0.0665 p99.9=0.0899 max=0.0899 mean=0.0289 |

- release lag: 240 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/proto-builder/acceptance-1/6-honesty/direct/olg', 'samples': 60, 'busy_max': 22.951435538616273, 'busy_mean': 8.08, 'machine': 'c4-standard-16', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 0, 'busy_max': None, 'busy_p95': None}
- completeness: [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/proto-builder/acceptance-1/6-honesty/direct/olg', 'scheduled': 260, 'recorded': 260, 'interrupted': False}]
- health olg direct: gc_cycles=0 sched_latency_max_ms=0.098304 tcp=None
