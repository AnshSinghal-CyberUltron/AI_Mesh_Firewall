# Harness analysis (sut, policy=none) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| error_rate_le_budget | FAIL |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 7467 over 300 s = 24.89 req/s (configured 25.0)
- qualified: 7290 (24.3 req/s); expected blocks: 0; errors: 177 (rate 0.023704298915227)
- error reasons: {'incomplete': 177, 'unjoined': 177, 'http_403': 103, 'disposition_BLOCK': 103, 'stage_dispatch_S': 103, 'stage_out_S': 103, 'http_503': 74, 'disposition_missing': 74, 'stage_canon_missing': 74, 'stage_det_missing': 74, 'stage_sem_missing': 74, 'stage_resolve_missing': 74, 'stage_dispatch_missing': 74, 'stage_out_missing': 74, 'stage_audit_missing': 74}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=7467 p50=0.0836 p90=0.0959 p99=0.1118 p99.9=0.1254 max=0.1623 mean=0.0842
- join: {'joined': 7290, 'by_nonce_fallback': 0, 'provider_records': 9114, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=7290 p50=9.4861 p90=12.7164 p99=15.7722 p99.9=19.2626 max=20.6364 mean=8.897 |
| T_addon_first | n=7290 p50=9.4316 p90=12.8341 p99=25.8659 p99.9=33.1148 max=53.1044 mean=9.0784 |
| T_release_lag_max | n=735 p50=46.2034 p90=52.8675 p99=69.621 p99.9=73.257 max=73.257 mean=36.7659 |
| T_release_lag_max_arrival | n=735 p50=31.8932 p90=37.2338 p99=40.4305 p99.9=41.6475 max=41.6475 mean=26.1975 |
| T_fw_addon | n=7290 p50=9.6902 p90=14.4383 p99=52.8774 p99.9=69.621 max=73.257 mean=11.9712 |
| T_fw_addon_sse | n=5093 p50=9.7721 p90=31.1447 p99=53.625 p99.9=69.9491 max=73.257 mean=13.2754 |
| T_fw_addon_json | n=2197 p50=9.5265 p90=12.88 p99=16.2261 p99.9=19.7141 max=20.6364 mean=8.948 |
| client_ttft_sse | n=5093 p50=159.4474 p90=162.8795 p99=178.852 p99.9=183.8069 max=203.1113 mean=159.1861 |
| provider_sched_err_last | n=7290 p50=0.0519 p90=0.0883 p99=0.1041 p99.9=0.118 max=0.1277 mean=0.052 |
| provider_sched_err_max_per_stream | n=7290 p50=0.0746 p90=0.1111 p99=0.1292 p99.9=0.1627 max=0.214 mean=0.0726 |
| provider_write_max | n=7290 p50=0.0125 p90=0.0228 p99=0.0331 p99.9=0.0442 max=0.061 mean=0.0145 |

- release lag: 735 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22p-025/rv-pbu-lg-1/lg', 'samples': 300, 'busy_max': 16.205586195088063, 'busy_mean': 4.18, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22p-025/rv-pbu-lg-2/lg', 'samples': 300, 'busy_max': 17.026785494340977, 'busy_mean': 4.03, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 24.784621787591288, 'busy_p95': 8.150436462048328}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22p-025/rv-pbu-lg-1/lg', 'scheduled': 4631, 'recorded': 4631, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22p-025/rv-pbu-lg-2/lg', 'scheduled': 4708, 'recorded': 4708, 'interrupted': False}]
- health olg rv-pbu-lg-1: gc_cycles=0 sched_latency_max_ms=0.08192 tcp={'TcpRetransSegs': 1, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 1, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 559144, 'TcpInSegs': 713487}
- health olg rv-pbu-lg-2: gc_cycles=0 sched_latency_max_ms=0.08192 tcp={'TcpRetransSegs': 7, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 4, 'TcpExtTCPTimeouts': 4, 'TcpOutSegs': 558112, 'TcpInSegs': 726172}
- health synthprov rv-pbu-prov-1: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 7, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 7, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1475908, 'TcpInSegs': 1236897}
