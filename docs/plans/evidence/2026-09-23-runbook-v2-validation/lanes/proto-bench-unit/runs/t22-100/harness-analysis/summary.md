# Harness analysis (sut, policy=none) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| error_rate_le_budget | FAIL |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 30000 over 300 s = 100.0 req/s (configured 100.0)
- qualified: 28657 (95.52 req/s); expected blocks: 0; errors: 1343 (rate 0.04476666666666667)
- error reasons: {'incomplete': 1343, 'unjoined': 1343, 'http_503': 834, 'disposition_missing': 834, 'stage_canon_missing': 834, 'stage_det_missing': 834, 'stage_sem_missing': 834, 'stage_resolve_missing': 834, 'stage_dispatch_missing': 834, 'stage_out_missing': 834, 'stage_audit_missing': 834, 'http_403': 509, 'disposition_BLOCK': 509, 'stage_dispatch_S': 509, 'stage_out_S': 509, 'stage_sem_U': 4}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=30000 p50=0.0848 p90=0.0945 p99=0.1092 p99.9=0.1502 max=0.3103 mean=0.0854
- join: {'joined': 28657, 'by_nonce_fallback': 0, 'provider_records': 35874, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=28657 p50=13.7188 p90=17.1913 p99=20.4616 p99.9=26.5267 max=33.5258 mean=13.8082 |
| T_addon_first | n=28657 p50=13.6595 p90=17.3339 p99=30.7713 p99.9=37.3511 max=74.1341 mean=13.9518 |
| T_release_lag_max | n=2912 p50=51.8997 p90=57.1504 p99=75.4196 p99.9=80.1686 max=91.2201 mean=41.7264 |
| T_release_lag_max_arrival | n=2912 p50=37.3421 p90=42.2741 p99=46.6015 p99.9=49.0393 max=52.3692 mean=31.5403 |
| T_fw_addon | n=28657 p50=14.0411 p90=19.47 p99=57.2216 p99.9=75.5763 max=91.2201 mean=16.9224 |
| T_fw_addon_sse | n=20091 p50=14.1784 p90=36.7207 p99=58.2609 p99.9=76.9229 max=91.2201 mean=18.2152 |
| T_fw_addon_json | n=8566 p50=13.7982 p90=17.3361 p99=20.4366 p99.9=26.4047 max=30.1502 mean=13.8902 |
| client_ttft_sse | n=20091 p50=163.6513 p90=167.386 p99=183.0925 p99.9=187.9635 max=224.1915 mean=164.0236 |
| provider_sched_err_last | n=28657 p50=0.0433 p90=0.0871 p99=0.1117 p99.9=0.1295 max=0.1535 mean=0.0454 |
| provider_sched_err_max_per_stream | n=28657 p50=0.0911 p90=0.1326 p99=0.1705 p99.9=0.1971 max=0.2297 mean=0.0871 |
| provider_write_max | n=28657 p50=0.0175 p90=0.0251 p99=0.0343 p99.9=0.0464 max=0.1229 mean=0.0174 |

- release lag: 2912 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/t22-100/rv-pbu-lg-1/lg', 'samples': 300, 'busy_max': 8.3481663966959, 'busy_mean': 5.19, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/t22-100/rv-pbu-lg-2/lg', 'samples': 300, 'busy_max': 5.4136373489669705, 'busy_mean': 5.02, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 9.274000242588532, 'busy_p95': 8.932480711127944}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/t22-100/rv-pbu-lg-1/lg', 'scheduled': 18750, 'recorded': 18750, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/t22-100/rv-pbu-lg-2/lg', 'scheduled': 18750, 'recorded': 18750, 'interrupted': False}]
- health olg rv-pbu-lg-1: gc_cycles=0 sched_latency_max_ms=0.08192 tcp={'TcpRetransSegs': 15, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 15, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1759416, 'TcpInSegs': 2788385}
- health olg rv-pbu-lg-2: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 3, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 3, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1788120, 'TcpInSegs': 2848691}
- health synthprov rv-pbu-prov-1: gc_cycles=0 sched_latency_max_ms=0.26214400000000004 tcp={'TcpRetransSegs': 28, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 28, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 5784554, 'TcpInSegs': 4116383}
