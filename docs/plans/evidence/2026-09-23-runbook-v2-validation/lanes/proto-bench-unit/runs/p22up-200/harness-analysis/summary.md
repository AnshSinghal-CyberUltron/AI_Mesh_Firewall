# Harness analysis (sut, policy=none) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| error_rate_le_budget | FAIL |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 60460 over 300 s = 201.53 req/s (configured 200.0)
- qualified: 59406 (198.02 req/s); expected blocks: 0; errors: 1054 (rate 0.017433013562686072)
- error reasons: {'http_403': 1054, 'incomplete': 1054, 'unjoined': 1054, 'disposition_BLOCK': 1054, 'stage_dispatch_S': 1054, 'stage_out_S': 1054, 'stage_sem_U': 16}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=60460 p50=0.0824 p90=0.0928 p99=0.1045 p99.9=0.124 max=0.2453 mean=0.0815
- join: {'joined': 59406, 'by_nonce_fallback': 0, 'provider_records': 74259, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=59406 p50=11.5383 p90=17.2275 p99=24.9655 p99.9=31.9388 max=40.357 mean=11.8705 |
| T_addon_first | n=59406 p50=11.494 p90=17.4956 p99=29.7223 p99.9=38.8506 max=84.3734 mean=12.0106 |
| T_release_lag_max | n=6058 p50=48.5411 p90=58.1269 p99=74.7486 p99.9=85.2131 max=94.1014 mean=40.2283 |
| T_release_lag_max_arrival | n=6058 p50=36.9503 p90=44.8715 p99=52.2986 p99.9=58.0005 max=62.4984 mean=31.5755 |
| T_fw_addon | n=59406 p50=12.0159 p90=23.0962 p99=58.2996 p99.9=74.8932 max=94.1014 mean=15.1008 |
| T_fw_addon_sse | n=41601 p50=12.2321 p90=37.6165 p99=61.0133 p99.9=76.1399 max=94.1014 mean=16.4467 |
| T_fw_addon_json | n=17805 p50=11.6183 p90=17.3472 p99=24.7323 p99.9=31.5805 max=40.357 mean=11.9561 |
| client_ttft_sse | n=41601 p50=161.485 p90=167.6204 p99=181.147 p99.9=189.9717 max=234.3769 mean=162.0756 |
| provider_sched_err_last | n=59406 p50=0.0355 p90=0.0868 p99=0.1154 p99.9=0.1454 max=0.223 mean=0.0413 |
| provider_sched_err_max_per_stream | n=59406 p50=0.1038 p90=0.1434 p99=0.1839 p99.9=0.2154 max=0.3709 mean=0.0946 |
| provider_write_max | n=59406 p50=0.0184 p90=0.025 p99=0.035 p99.9=0.0478 max=0.1899 mean=0.0182 |

- release lag: 6058 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22up-200/rv-pbu-lg-1/lg', 'samples': 300, 'busy_max': 11.558646288278773, 'busy_mean': 8.21, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22up-200/rv-pbu-lg-2/lg', 'samples': 300, 'busy_max': 8.771335682118664, 'busy_mean': 7.8, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 13.890354053885412, 'busy_p95': 13.676752877303322}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22up-200/rv-pbu-lg-1/lg', 'scheduled': 37736, 'recorded': 37736, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22up-200/rv-pbu-lg-2/lg', 'scheduled': 37795, 'recorded': 37795, 'interrupted': False}]
- health olg rv-pbu-lg-1: gc_cycles=0 sched_latency_max_ms=0.13107200000000002 tcp={'TcpRetransSegs': 41, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 41, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 3742242, 'TcpInSegs': 5822573}
- health olg rv-pbu-lg-2: gc_cycles=0 sched_latency_max_ms=0.13107200000000002 tcp={'TcpRetransSegs': 30, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 30, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 3748995, 'TcpInSegs': 5826772}
- health synthprov rv-pbu-prov-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 142, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 142, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 11959855, 'TcpInSegs': 8559519}
