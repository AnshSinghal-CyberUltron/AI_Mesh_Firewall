# Harness analysis (direct, policy=none) — PASS

| check | result |
|---|---|
| zero_schedule_drops | PASS |
| zero_errors | PASS |
| provider_p99_sched_err_within_tol | PASS |
| loadgen_cpu_max_lt_limit | PASS |
| run_valid | PASS |
| all_joined | PASS |

- offered: 160 over 8 s = 20.0 req/s (configured 20.0)
- qualified: 160 (20.0 req/s); expected blocks: 0; errors: 0 (rate 0.0)
- error reasons: {}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=160 p50=0.0679 p90=0.0943 p99=0.1958 p99.9=0.2044 max=0.2044 mean=0.0755
- join: {'joined': 160, 'by_nonce_fallback': 0, 'provider_records': 207, 'provider_rids_with_multiple_calls': 2, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=160 p50=0.1631 p90=0.3333 p99=0.5536 p99.9=0.5818 max=0.5818 mean=0.1895 |
| T_addon_first | n=160 p50=0.1626 p90=0.3157 p99=0.5818 p99.9=0.5869 max=0.5869 mean=0.1959 |
| T_release_lag_max | n=160 p50=0.2087 p90=0.4419 p99=0.5818 p99.9=0.5869 max=0.5869 mean=0.253 |
| T_release_lag_max_arrival | n=160 p50=0.2087 p90=0.4419 p99=0.5818 p99.9=0.5869 max=0.5869 mean=0.253 |
| T_fw_addon | n=160 p50=0.2087 p90=0.4419 p99=0.5818 p99.9=0.5869 max=0.5869 mean=0.253 |
| T_fw_addon_sse | n=112 p50=0.2255 p90=0.442 p99=0.5812 p99.9=0.5869 max=0.5869 mean=0.262 |
| T_fw_addon_json | n=48 p50=0.1939 p90=0.3972 p99=0.5818 p99.9=0.5818 max=0.5818 mean=0.2322 |
| client_ttft_sse | n=112 p50=150.1915 p90=150.351 p99=150.5914 p99.9=150.6366 max=150.6366 mean=150.2231 |
| provider_sched_err_last | n=160 p50=0.0254 p90=0.067 p99=0.1077 p99.9=0.1655 max=0.1655 mean=0.0312 |
| provider_sched_err_max_per_stream | n=160 p50=0.1087 p90=0.2334 p99=0.3128 p99.9=0.3135 max=0.3135 mean=0.1194 |
| provider_write_max | n=160 p50=0.0388 p90=0.0588 p99=0.0753 p99.9=0.3968 max=0.3968 mean=0.0432 |

- release lag: 160 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/harness-builder/local-e2e/run-direct', 'samples': 8, 'busy_max': 33.070348454963835, 'busy_mean': 13.84, 'machine': 'c4-standard-16', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 52, 'busy_max': 46.82230869001297, 'busy_p95': 33.741146168705725}
- completeness: [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/harness-builder/local-e2e/run-direct', 'scheduled': 200, 'recorded': 200, 'interrupted': False}]
