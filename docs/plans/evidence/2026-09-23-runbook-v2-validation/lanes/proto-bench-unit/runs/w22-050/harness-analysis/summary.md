# Harness analysis (sut, policy=none) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| error_rate_le_budget | FAIL |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 15000 over 300 s = 50.0 req/s (configured 50.0)
- qualified: 14609 (48.7 req/s); expected blocks: 0; errors: 391 (rate 0.026066666666666665)
- error reasons: {'incomplete': 391, 'unjoined': 391, 'http_403': 387, 'disposition_BLOCK': 387, 'stage_dispatch_S': 387, 'stage_out_S': 387, 'http_503': 4, 'disposition_missing': 4, 'stage_canon_missing': 4, 'stage_det_missing': 4, 'stage_sem_missing': 4, 'stage_resolve_missing': 4, 'stage_dispatch_missing': 4, 'stage_out_missing': 4, 'stage_audit_missing': 4}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=15000 p50=0.0874 p90=0.1004 p99=0.1162 p99.9=0.1463 max=0.3438 mean=0.0875
- join: {'joined': 14609, 'by_nonce_fallback': 0, 'provider_records': 18271, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=14609 p50=14.9658 p90=15.9723 p99=20.0681 p99.9=23.3244 max=28.516 mean=15.0564 |
| T_addon_first | n=14609 p50=14.9277 p90=16.0868 p99=33.4894 p99.9=37.1665 max=54.7729 mean=15.2226 |
| T_release_lag_max | n=1442 p50=54.9608 p90=59.5407 p99=75.4952 p99.9=81.8396 max=82.7331 mean=45.8457 |
| T_release_lag_max_arrival | n=1442 p50=41.6989 p90=44.2281 p99=46.5013 p99.9=48.9523 max=48.9617 mean=34.7663 |
| T_fw_addon | n=14609 p50=15.2168 p90=18.7175 p99=59.3385 p99.9=75.4952 max=82.7331 mean=18.4927 |
| T_fw_addon_sse | n=10227 p50=15.279 p90=53.0472 p99=73.2287 p99.9=75.6208 max=82.7331 mean=19.914 |
| T_fw_addon_json | n=4382 p50=15.0864 p90=16.0508 p99=20.233 p99.9=23.4357 max=27.2183 mean=15.1756 |
| client_ttft_sse | n=10227 p50=164.8949 p90=166.1698 p99=184.4878 p99.9=187.5475 max=204.8227 mean=165.2824 |
| provider_sched_err_last | n=14609 p50=0.0279 p90=0.0955 p99=0.167 p99.9=0.3046 max=0.4958 mean=0.0411 |
| provider_sched_err_max_per_stream | n=14609 p50=0.1701 p90=0.3176 p99=0.4985 p99.9=0.8156 max=1.0701 mean=0.1723 |
| provider_write_max | n=14609 p50=0.0243 p90=0.0326 p99=0.0465 p99.9=0.0609 max=0.0856 mean=0.0253 |

- release lag: 1442 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/w22-050/rv-pbu-lg-1/lg', 'samples': 300, 'busy_max': 15.830674624345864, 'busy_mean': 5.66, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/w22-050/rv-pbu-lg-2/lg', 'samples': 300, 'busy_max': 18.2408036745897, 'busy_mean': 5.5, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 18.86728936513016, 'busy_p95': 7.033341602078047}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/w22-050/rv-pbu-lg-1/lg', 'scheduled': 9375, 'recorded': 9375, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/w22-050/rv-pbu-lg-2/lg', 'scheduled': 9375, 'recorded': 9375, 'interrupted': False}]
- health olg rv-pbu-lg-1: gc_cycles=0 sched_latency_max_ms=0.16384 tcp={'TcpRetransSegs': 2, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 2, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1584067, 'TcpInSegs': 2530985}
- health olg rv-pbu-lg-2: gc_cycles=0 sched_latency_max_ms=0.13107200000000002 tcp={'TcpRetransSegs': 1, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 1, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1583535, 'TcpInSegs': 2531242}
- health synthprov rv-pbu-prov-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 14, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 9, 'TcpExtTCPTimeouts': 6, 'TcpOutSegs': 5175452, 'TcpInSegs': 2907213}
