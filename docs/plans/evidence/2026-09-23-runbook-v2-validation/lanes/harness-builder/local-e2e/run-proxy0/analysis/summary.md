# Harness analysis (sut, policy=none) — PASS

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | PASS |
| error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 160 over 8 s = 20.0 req/s (configured 20.0)
- qualified: 160 (20.0 req/s); expected blocks: 0; errors: 0 (rate 0.0)
- error reasons: {}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=160 p50=0.1136 p90=0.1297 p99=0.2513 p99.9=0.2739 max=0.2739 mean=0.1166
- join: {'joined': 160, 'by_nonce_fallback': 0, 'provider_records': 200, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=160 p50=0.3231 p90=0.4557 p99=0.7148 p99.9=0.7271 max=0.7271 mean=0.3424 |
| T_addon_first | n=160 p50=0.3576 p90=0.4854 p99=0.7271 p99.9=0.9969 max=0.9969 mean=0.3815 |
| T_release_lag_max | n=160 p50=0.3959 p90=0.6055 p99=0.9046 p99.9=0.9969 max=0.9969 mean=0.4397 |
| T_release_lag_max_arrival | n=160 p50=0.3959 p90=0.6055 p99=0.9046 p99.9=0.9969 max=0.9969 mean=0.4397 |
| T_fw_addon | n=160 p50=0.3959 p90=0.6055 p99=0.9046 p99.9=0.9969 max=0.9969 mean=0.4397 |
| T_fw_addon_sse | n=112 p50=0.4176 p90=0.6221 p99=0.9046 p99.9=0.9969 max=0.9969 mean=0.4536 |
| T_fw_addon_json | n=48 p50=0.3837 p90=0.5018 p99=0.7271 p99.9=0.7271 max=0.7271 mean=0.407 |
| client_ttft_sse | n=112 p50=150.3821 p90=150.6006 p99=150.7508 p99.9=151.0718 max=151.0718 mean=150.4143 |
| provider_sched_err_last | n=160 p50=0.0428 p90=0.0821 p99=0.1156 p99.9=0.1501 max=0.1501 mean=0.0446 |
| provider_sched_err_max_per_stream | n=160 p50=0.1302 p90=0.2482 p99=0.2846 p99.9=0.301 max=0.301 mean=0.1355 |
| provider_write_max | n=160 p50=0.0329 p90=0.0521 p99=0.204 p99.9=0.2144 max=0.2144 mean=0.0392 |

- release lag: 160 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/harness-builder/local-e2e/run-proxy0', 'samples': 8, 'busy_max': 17.78501628664495, 'busy_mean': 4.77, 'machine': 'c4-standard-16', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 0, 'busy_max': None, 'busy_p95': None}
- completeness: [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/harness-builder/local-e2e/run-proxy0', 'scheduled': 200, 'recorded': 200, 'interrupted': False}]
