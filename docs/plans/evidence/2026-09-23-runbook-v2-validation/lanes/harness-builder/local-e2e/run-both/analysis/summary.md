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
- schedule drops (>5.0 ms): 0; lateness ms: n=160 p50=0.1053 p90=0.127 p99=0.1929 p99.9=0.2135 max=0.2135 mean=0.1089
- join: {'joined': 160, 'by_nonce_fallback': 0, 'provider_records': 200, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=160 p50=5.4426 p90=5.5731 p99=5.8163 p99.9=6.1551 max=6.1551 mean=5.4499 |
| T_addon_first | n=160 p50=5.4568 p90=5.6346 p99=5.8582 p99.9=6.2546 max=6.2546 mean=5.4883 |
| T_release_lag_max | n=160 p50=35.3874 p90=35.4896 p99=36.1705 p99.9=37.3246 max=37.3246 mean=26.4725 |
| T_release_lag_max_arrival | n=160 p50=35.3874 p90=35.4896 p99=36.1705 p99.9=37.3246 max=37.3246 mean=26.4725 |
| T_fw_addon | n=160 p50=35.3874 p90=35.4896 p99=36.1705 p99.9=37.3246 max=37.3246 mean=26.4725 |
| T_fw_addon_sse | n=112 p50=35.4143 p90=35.5662 p99=36.1705 p99.9=37.3246 max=37.3246 mean=35.452 |
| T_fw_addon_json | n=48 p50=5.4907 p90=5.6346 p99=5.8163 p99.9=5.8163 max=5.8163 mean=5.5202 |
| client_ttft_sse | n=112 p50=155.4925 p90=155.6729 p99=155.8856 p99.9=156.2867 max=156.2867 mean=155.524 |
| provider_sched_err_last | n=160 p50=0.062 p90=0.0979 p99=0.1671 p99.9=0.2751 max=0.2751 mean=0.0591 |
| provider_sched_err_max_per_stream | n=160 p50=0.1228 p90=0.2751 p99=0.3106 p99.9=0.3632 max=0.3632 mean=0.1399 |
| provider_write_max | n=160 p50=0.036 p90=0.0577 p99=0.0805 p99.9=0.2145 max=0.2145 mean=0.0405 |

- release lag: 160 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/harness-builder/local-e2e/run-both', 'samples': 8, 'busy_max': 38.48054354539839, 'busy_mean': 16.9, 'machine': 'c4-standard-16', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 0, 'busy_max': None, 'busy_p95': None}
- completeness: [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/harness-builder/local-e2e/run-both', 'scheduled': 200, 'recorded': 200, 'interrupted': False}]
