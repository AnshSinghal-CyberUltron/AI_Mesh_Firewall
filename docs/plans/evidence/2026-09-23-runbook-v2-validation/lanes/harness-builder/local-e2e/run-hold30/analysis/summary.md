# Harness analysis (sut, policy=none) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 160 over 8 s = 20.0 req/s (configured 20.0)
- qualified: 160 (20.0 req/s); expected blocks: 0; errors: 0 (rate 0.0)
- error reasons: {}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=160 p50=0.1068 p90=0.1505 p99=0.2174 p99.9=0.2703 max=0.2703 mean=0.1177
- join: {'joined': 160, 'by_nonce_fallback': 0, 'provider_records': 200, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=160 p50=0.3571 p90=0.6475 p99=1.0179 p99.9=1.4073 max=1.4073 mean=0.4102 |
| T_addon_first | n=160 p50=0.3847 p90=0.6628 p99=1.0639 p99.9=1.6553 max=1.6553 mean=0.4676 |
| T_release_lag_max | n=160 p50=30.2837 p90=30.5129 p99=30.996 p99.9=31.6148 max=31.6148 mean=21.4222 |
| T_release_lag_max_arrival | n=160 p50=30.2837 p90=30.5129 p99=30.996 p99.9=31.6148 max=31.6148 mean=21.4222 |
| T_fw_addon | n=160 p50=30.2837 p90=30.5129 p99=30.996 p99.9=31.6148 max=31.6148 mean=21.4222 |
| T_fw_addon_sse | n=112 p50=30.3172 p90=30.6687 p99=30.996 p99.9=31.6148 max=31.6148 mean=30.3973 |
| T_fw_addon_json | n=48 p50=0.407 p90=0.6475 p99=1.0179 p99.9=1.0179 max=1.0179 mean=0.4802 |
| client_ttft_sse | n=112 p50=150.4041 p90=150.7968 p99=151.1111 p99.9=151.7283 max=151.7283 mean=150.5054 |
| provider_sched_err_last | n=160 p50=0.0493 p90=0.1129 p99=0.1511 p99.9=0.2771 max=0.2771 mean=0.0572 |
| provider_sched_err_max_per_stream | n=160 p50=0.1343 p90=0.2622 p99=0.318 p99.9=0.3503 max=0.3503 mean=0.1385 |
| provider_write_max | n=160 p50=0.0393 p90=0.0566 p99=0.1743 p99.9=0.2113 max=0.2113 mean=0.0444 |

- release lag: 160 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/harness-builder/local-e2e/run-hold30', 'samples': 8, 'busy_max': 36.2796833773087, 'busy_mean': 20.71, 'machine': 'c4-standard-16', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 0, 'busy_max': None, 'busy_p95': None}
- completeness: [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/harness-builder/local-e2e/run-hold30', 'scheduled': 200, 'recorded': 200, 'interrupted': False}]
