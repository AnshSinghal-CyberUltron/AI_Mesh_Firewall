# Harness analysis (sut, policy=none) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| error_rate_le_budget | FAIL |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 7500 over 300 s = 25.0 req/s (configured 25.0)
- qualified: 7328 (24.43 req/s); expected blocks: 0; errors: 172 (rate 0.022933333333333333)
- error reasons: {'http_403': 172, 'incomplete': 172, 'unjoined': 172, 'disposition_BLOCK': 172, 'stage_dispatch_S': 172, 'stage_out_S': 172}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=7500 p50=0.0861 p90=0.0984 p99=0.1157 p99.9=0.1812 max=0.2905 mean=0.0865
- join: {'joined': 7328, 'by_nonce_fallback': 0, 'provider_records': 9156, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=7328 p50=14.5812 p90=15.1085 p99=15.5236 p99.9=16.8741 max=22.4241 mean=14.5364 |
| T_addon_first | n=7328 p50=14.4694 p90=15.073 p99=16.6561 p99.9=34.6446 max=53.8967 mean=14.6018 |
| T_release_lag_max | n=7328 p50=54.1441 p90=55.1601 p99=74.6736 p99.9=75.3849 max=94.5037 mean=43.922 |
| T_release_lag_max_arrival | n=7328 p50=34.8521 p90=35.447 p99=41.5821 p99.9=42.5115 max=48.4063 mean=28.9881 |
| T_fw_addon | n=7328 p50=54.1441 p90=55.1601 p99=74.6736 p99.9=75.3849 max=94.5037 mean=43.922 |
| T_fw_addon_sse | n=5129 p50=54.4991 p90=73.2662 p99=74.7521 p99.9=76.6858 max=94.5037 mean=56.4599 |
| T_fw_addon_json | n=2199 p50=14.7434 p90=15.2335 p99=15.6238 p99.9=16.7366 max=17.2524 mean=14.6785 |
| client_ttft_sse | n=5129 p50=164.4129 p90=164.9683 p99=183.642 p99.9=184.871 max=203.9061 mean=164.6139 |
| provider_sched_err_last | n=7328 p50=0.0311 p90=0.0965 p99=0.2181 p99.9=0.3438 max=0.548 mean=0.0442 |
| provider_sched_err_max_per_stream | n=7328 p50=0.2668 p90=0.4222 p99=0.7249 p99.9=1.1285 max=1.259 mean=0.2395 |
| provider_write_max | n=7328 p50=0.0245 p90=0.0356 p99=0.0484 p99.9=0.0621 max=0.1011 mean=0.0261 |

- release lag: 7328 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/li-w025/rv-pbu-lg-3/lg', 'samples': 300, 'busy_max': 15.704245531789763, 'busy_mean': 4.88, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/li-w025/rv-pbu-lg-4/lg', 'samples': 300, 'busy_max': 18.43142543583435, 'busy_mean': 4.8, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 19.460239618158127, 'busy_p95': 5.8707267265822765}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/li-w025/rv-pbu-lg-3/lg', 'scheduled': 4688, 'recorded': 4688, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/li-w025/rv-pbu-lg-4/lg', 'scheduled': 4687, 'recorded': 4687, 'interrupted': False}]
- health olg rv-pbu-lg-3: gc_cycles=0 sched_latency_max_ms=0.08192 tcp={'TcpRetransSegs': 5, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 6, 'TcpOutSegs': 744281, 'TcpInSegs': 1269937}
- health olg rv-pbu-lg-4: gc_cycles=0 sched_latency_max_ms=0.16384 tcp={'TcpRetransSegs': 3, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 3, 'TcpOutSegs': 749510, 'TcpInSegs': 1267306}
- health synthprov rv-pbu-prov-2: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 21, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 4, 'TcpExtTCPTimeouts': 18, 'TcpOutSegs': 2593671, 'TcpInSegs': 1613181}
