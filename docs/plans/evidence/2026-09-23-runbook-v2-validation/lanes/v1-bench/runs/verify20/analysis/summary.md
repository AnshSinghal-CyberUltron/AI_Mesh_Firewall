# Harness analysis (sut, policy=none) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 20 over 10 s = 2.0 req/s (configured 2.0)
- qualified: 20 (2.0 req/s); expected blocks: 0; errors: 0 (rate 0.0)
- error reasons: {}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=20 p50=0.0895 p90=0.1115 p99=0.2383 p99.9=0.2383 max=0.2383 mean=0.0959
- join: {'joined': 20, 'by_nonce_fallback': 0, 'provider_records': 20, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=20 p50=84.2977 p90=98.7019 p99=133.5045 p99.9=133.5045 max=133.5045 mean=85.3816 |
| T_addon_first | n=20 p50=665.1531 p90=776.2322 p99=1021.6015 p99.9=1021.6015 max=1021.6015 mean=524.9395 |
| T_release_lag_max | n=1 p50=64.0672 p90=64.0672 p99=64.0672 p99.9=64.0672 max=64.0672 mean=64.0672 |
| T_release_lag_max_arrival | n=1 p50=64.0672 p90=64.0672 p99=64.0672 p99.9=64.0672 max=64.0672 mean=64.0672 |
| T_fw_addon | n=20 p50=665.1531 p90=776.2322 p99=1021.6015 p99.9=1021.6015 max=1021.6015 mean=524.9395 |
| T_fw_addon_sse | n=14 p50=730.922 p90=788.9233 p99=1021.6015 p99.9=1021.6015 max=1021.6015 mean=716.3554 |
| T_fw_addon_json | n=6 p50=68.262 p90=102.5053 p99=102.5053 p99.9=102.5053 max=102.5053 mean=78.3024 |
| client_ttft_sse | n=14 p50=881.0207 p90=939.0083 p99=1171.6516 p99.9=1171.6516 max=1171.6516 mean=866.4194 |
| provider_sched_err_last | n=20 p50=0.0503 p90=0.0902 p99=0.0997 p99.9=0.0997 max=0.0997 mean=0.0531 |
| provider_sched_err_max_per_stream | n=20 p50=0.059 p90=0.0997 p99=0.11 p99.9=0.11 max=0.11 mean=0.0641 |
| provider_write_max | n=20 p50=0.0306 p90=0.069 p99=0.2143 p99.9=0.2143 max=0.2143 mean=0.0454 |

- release lag: 1 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/v1-bench/runs/verify20/lg-v1aug', 'samples': 9, 'busy_max': 0.7761966364812412, 'busy_mean': 0.13, 'machine': 'c4-standard-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 32, 'busy_max': 14.037267080745341, 'busy_p95': 13.459119496855344}
- completeness: [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/v1-bench/runs/verify20/lg-v1aug', 'scheduled': 20, 'recorded': 20, 'interrupted': False}]
