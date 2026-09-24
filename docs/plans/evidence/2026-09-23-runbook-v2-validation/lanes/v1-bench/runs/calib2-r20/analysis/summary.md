# Harness analysis (sut, policy=none) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 1200 over 60 s = 20.0 req/s (configured 20.0)
- qualified: 1200 (20.0 req/s); expected blocks: 0; errors: 0 (rate 0.0)
- error reasons: {}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=1200 p50=0.0991 p90=0.1098 p99=0.1217 p99.9=0.1423 max=0.1872 mean=0.0924
- join: {'joined': 1200, 'by_nonce_fallback': 0, 'provider_records': 1600, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=1200 p50=283.8048 p90=2009.2995 p99=4117.2334 p99.9=4956.808 max=4987.7402 mean=742.1788 |
| T_addon_first | n=1200 p50=764.2342 p90=998.2894 p99=1208.7256 p99.9=1297.3463 max=1331.1495 mean=636.0727 |
| T_release_lag_max | n=134 p50=1140.7365 p90=2233.5051 p99=3808.3279 p99.9=4369.8525 max=4369.8525 mean=1037.8819 |
| T_release_lag_max_arrival | n=134 p50=1140.7365 p90=2233.5051 p99=3808.3279 p99.9=4369.8525 max=4369.8525 mean=1037.8819 |
| T_fw_addon | n=1200 p50=819.4184 p90=2015.4047 p99=4117.2334 p99.9=4956.808 max=4987.7402 mean=952.8564 |
| T_fw_addon_sse | n=840 p50=990.1052 p90=2326.5066 p99=4322.0053 p99.9=4987.7402 max=4987.7402 mean=1281.5613 |
| T_fw_addon_json | n=360 p50=171.2606 p90=257.0013 p99=498.9763 p99.9=526.2839 max=526.2839 mean=185.8785 |
| client_ttft_sse | n=840 p50=984.1425 p90=1188.8838 p99=1381.2263 p99.9=1481.2214 max=1481.2214 mean=979.0639 |
| provider_sched_err_last | n=1200 p50=0.0549 p90=0.0894 p99=0.1023 p99.9=0.1115 max=0.112 mean=0.053 |
| provider_sched_err_max_per_stream | n=1200 p50=0.0709 p90=0.1101 p99=0.129 p99.9=0.3428 max=0.4396 mean=0.0715 |
| provider_write_max | n=1200 p50=0.0165 p90=0.036 p99=0.0499 p99.9=0.0633 max=0.1471 mean=0.0202 |

- release lag: 134 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/v1-bench/runs/calib2-r20/lg-v1aug', 'samples': 60, 'busy_max': 12.596401028277636, 'busy_mean': 2.03, 'machine': 'c4-standard-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 106, 'busy_max': 13.376623376623375, 'busy_p95': 0.5194805194805197}
- completeness: [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/v1-bench/runs/calib2-r20/lg-v1aug', 'scheduled': 1600, 'recorded': 1600, 'interrupted': False}]
