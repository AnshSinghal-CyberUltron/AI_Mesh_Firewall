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
- qualified: 7392 (24.64 req/s); expected blocks: 0; errors: 108 (rate 0.0144)
- error reasons: {'incomplete': 108, 'unjoined': 108, 'http_403': 104, 'disposition_BLOCK': 104, 'stage_dispatch_S': 104, 'stage_out_S': 104, 'http_503': 4, 'disposition_missing': 4, 'stage_canon_missing': 4, 'stage_det_missing': 4, 'stage_sem_missing': 4, 'stage_resolve_missing': 4, 'stage_dispatch_missing': 4, 'stage_out_missing': 4, 'stage_audit_missing': 4, 'stage_sem_U': 3}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=7500 p50=0.0924 p90=0.1084 p99=0.1254 p99.9=0.1438 max=0.1779 mean=0.0932
- join: {'joined': 7392, 'by_nonce_fallback': 0, 'provider_records': 9238, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=7392 p50=9.4541 p90=11.708 p99=14.1715 p99.9=18.6387 max=20.7925 mean=8.8171 |
| T_addon_first | n=7392 p50=9.4181 p90=12.6409 p99=25.9716 p99.9=32.9626 max=49.8712 mean=9.0094 |
| T_release_lag_max | n=730 p50=46.1338 p90=51.9435 p99=70.0984 p99.9=72.9708 max=72.9708 mean=35.9798 |
| T_release_lag_max_arrival | n=730 p50=30.921 p90=36.1528 p99=39.6316 p99.9=41.3587 max=41.3587 mean=25.3132 |
| T_fw_addon | n=7392 p50=9.6953 p90=13.7314 p99=51.9435 p99.9=70.0984 max=72.9708 mean=11.7669 |
| T_fw_addon_sse | n=5169 p50=9.7671 p90=29.6988 p99=52.9033 p99.9=70.2608 max=72.9708 mean=13.0149 |
| T_fw_addon_json | n=2223 p50=9.5451 p90=11.8395 p99=14.1043 p99.9=16.8376 max=17.1383 mean=8.8648 |
| client_ttft_sse | n=5169 p50=159.4243 p90=162.754 p99=176.7285 p99.9=183.6507 max=199.8796 mean=159.1194 |
| provider_sched_err_last | n=7392 p50=0.0452 p90=0.0884 p99=0.1136 p99.9=0.2201 max=0.3295 mean=0.0472 |
| provider_sched_err_max_per_stream | n=7392 p50=0.0913 p90=0.2272 p99=0.3422 p99.9=0.5154 max=0.6182 mean=0.1071 |
| provider_write_max | n=7392 p50=0.0178 p90=0.0255 p99=0.0364 p99.9=0.0484 max=0.0882 mean=0.0178 |

- release lag: 730 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/h22-025/rv-pbu-lg-1/lg', 'samples': 300, 'busy_max': 7.149018243694439, 'busy_mean': 4.14, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/h22-025/rv-pbu-lg-2/lg', 'samples': 300, 'busy_max': 4.373411025437413, 'busy_mean': 4.02, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 15.593106675093104, 'busy_p95': 5.552692972916451}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/h22-025/rv-pbu-lg-1/lg', 'scheduled': 4688, 'recorded': 4688, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/h22-025/rv-pbu-lg-2/lg', 'scheduled': 4687, 'recorded': 4687, 'interrupted': False}]
- health olg rv-pbu-lg-1: gc_cycles=0 sched_latency_max_ms=0.057344 tcp={'TcpRetransSegs': 6, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 1, 'TcpExtTCPTimeouts': 6, 'TcpOutSegs': 552275, 'TcpInSegs': 729549}
- health olg rv-pbu-lg-2: gc_cycles=0 sched_latency_max_ms=0.13107200000000002 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 550946, 'TcpInSegs': 728432}
- health synthprov rv-pbu-prov-1: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 2, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 2, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1495563, 'TcpInSegs': 1218134}
