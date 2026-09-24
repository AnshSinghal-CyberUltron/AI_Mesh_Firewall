# Harness analysis (sut, policy=none) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 300 over 300 s = 1.0 req/s (configured 1.0)
- qualified: 300 (1.0 req/s); expected blocks: 0; errors: 0 (rate 0.0)
- error reasons: {}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=300 p50=0.1562 p90=0.2054 p99=0.274 p99.9=0.2922 max=0.2922 mean=0.1616
- join: {'joined': 300, 'by_nonce_fallback': 0, 'provider_records': 375, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=300 p50=85.7935 p90=110.496 p99=129.0894 p99.9=256.2864 max=256.2864 mean=86.174 |
| T_addon_first | n=300 p50=696.0576 p90=816.3311 p99=1033.148 p99.9=1128.8492 max=1128.8492 mean=522.5357 |
| T_release_lag_max | n=26 p50=904.9659 p90=1122.8855 p99=1138.8334 p99.9=1138.8334 max=1138.8334 mean=672.4762 |
| T_release_lag_max_arrival | n=26 p50=904.9659 p90=1122.8855 p99=1138.8334 p99.9=1138.8334 max=1138.8334 mean=672.4762 |
| T_fw_addon | n=300 p50=709.0916 p90=891.5205 p99=1122.8855 p99.9=1138.8334 max=1138.8334 mean=541.454 |
| T_fw_addon_sse | n=210 p50=747.2535 p90=974.4219 p99=1128.8492 p99.9=1138.8334 max=1138.8334 mean=741.2245 |
| T_fw_addon_json | n=90 p50=74.7127 p90=94.8516 p99=111.2235 p99.9=111.2235 max=111.2235 mean=75.3227 |
| client_ttft_sse | n=210 p50=890.4518 p90=1017.69 p99=1253.7193 p99.9=1278.8976 max=1278.8976 mean=864.2516 |
| provider_sched_err_last | n=300 p50=0.0512 p90=0.0903 p99=0.0993 p99.9=0.2281 max=0.2281 mean=0.0525 |
| provider_sched_err_max_per_stream | n=300 p50=0.0579 p90=0.101 p99=0.247 p99.9=0.2972 max=0.2972 mean=0.0644 |
| provider_write_max | n=300 p50=0.0225 p90=0.0347 p99=0.0469 p99.9=0.0502 max=0.0502 mean=0.0237 |

- release lag: 26 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/v1-bench/runs/N-r1/lg-v1aug', 'samples': 300, 'busy_max': 18.46726148566853, 'busy_mean': 2.71, 'machine': 'c4-standard-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 401, 'busy_max': 14.071537612096098, 'busy_p95': 3.6313287261290017}
- completeness: [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/v1-bench/runs/N-r1/lg-v1aug', 'scheduled': 375, 'recorded': 375, 'interrupted': False}]
- health olg N-r1: gc_cycles=0 sched_latency_max_ms=0.098304 tcp=None
- health synthprov rv-v1-prov-2: gc_cycles=0 sched_latency_max_ms=0.16384 tcp={'TcpRetransSegs': 24, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 24, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 62055, 'TcpInSegs': 61440}
