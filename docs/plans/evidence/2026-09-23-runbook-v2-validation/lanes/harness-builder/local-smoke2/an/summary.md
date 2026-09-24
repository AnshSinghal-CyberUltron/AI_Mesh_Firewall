# Harness analysis (direct, policy=none) — PASS

| check | result |
|---|---|
| zero_schedule_drops | PASS |
| zero_errors | PASS |
| provider_p99_sched_err_within_tol | PASS |
| loadgen_cpu_max_lt_limit | PASS |
| run_valid | PASS |
| all_joined | PASS |

- offered: 120 over 4 s = 30.0 req/s (configured 30.0)
- qualified: 120 (30.0 req/s); expected blocks: 0; errors: 0 (rate 0.0)
- error reasons: {}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=120 p50=0.0887 p90=0.133 p99=0.1941 p99.9=0.2299 max=0.2299 mean=0.0942
- join: {'joined': 120, 'by_nonce_fallback': 0, 'provider_records': 150, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=120 p50=0.1749 p90=0.3168 p99=0.6959 p99.9=4.8802 max=4.8802 mean=0.2371 |
| T_addon_first | n=120 p50=0.1749 p90=0.3198 p99=0.6959 p99.9=4.897 max=4.897 mean=0.2435 |
| T_release_lag_max | n=15 p50=0.1881 p90=0.3227 p99=0.3318 p99.9=0.3318 max=0.3318 mean=0.2183 |
| T_release_lag_max_arrival | n=15 p50=0.1881 p90=0.3227 p99=0.3318 p99.9=0.3318 max=0.3318 mean=0.2183 |
| T_fw_addon | n=120 p50=0.1749 p90=0.3213 p99=0.6959 p99.9=4.897 max=4.897 mean=0.2458 |
| T_fw_addon_sse | n=84 p50=0.1293 p90=0.3118 p99=4.897 p99.9=4.897 max=4.897 mean=0.2395 |
| T_fw_addon_json | n=36 p50=0.2701 p90=0.3678 p99=0.6959 p99.9=0.6959 max=0.6959 mean=0.2605 |
| client_ttft_sse | n=84 p50=150.1565 p90=150.3598 p99=154.9953 p99.9=154.9953 max=154.9953 mean=150.283 |
| provider_sched_err_last | n=120 p50=0.0345 p90=0.0781 p99=0.099 p99.9=0.1014 max=0.1014 mean=0.0407 |
| provider_sched_err_max_per_stream | n=120 p50=0.0704 p90=0.1171 p99=0.1746 p99.9=0.1813 max=0.1813 mean=0.0725 |
| provider_write_max | n=120 p50=0.0298 p90=0.0496 p99=0.0887 p99.9=0.1006 max=0.1006 mean=0.0349 |

- release lag: 15 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/harness-builder/local-smoke2/lg', 'samples': 4, 'busy_max': 8.209437621202332, 'busy_mean': 4.21, 'machine': 'c4-standard-16', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 0, 'busy_max': None, 'busy_p95': None}
- completeness: [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/harness-builder/local-smoke2/lg', 'scheduled': 150, 'recorded': 150, 'interrupted': False}]
