# Harness analysis (sut, policy=none) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| error_rate_le_budget | FAIL |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 23400 over 300 s = 78.0 req/s (configured 78.0)
- qualified: 22974 (76.58 req/s); expected blocks: 0; errors: 426 (rate 0.018205128205128204)
- error reasons: {'incomplete': 426, 'unjoined': 426, 'http_403': 410, 'disposition_BLOCK': 410, 'stage_dispatch_S': 410, 'stage_out_S': 410, 'http_503': 16, 'disposition_missing': 16, 'stage_canon_missing': 16, 'stage_det_missing': 16, 'stage_sem_missing': 16, 'stage_resolve_missing': 16, 'stage_dispatch_missing': 16, 'stage_out_missing': 16, 'stage_audit_missing': 16}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=23400 p50=0.0841 p90=0.0938 p99=0.1048 p99.9=0.1237 max=0.2452 mean=0.0839
- join: {'joined': 22974, 'by_nonce_fallback': 0, 'provider_records': 28747, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=22974 p50=9.5967 p90=12.4546 p99=16.0177 p99.9=20.8143 max=43.0663 mean=9.0572 |
| T_addon_first | n=22974 p50=9.5479 p90=12.7959 p99=25.7312 p99.9=33.3634 max=51.2636 mean=9.2185 |
| T_release_lag_max | n=2283 p50=46.1399 p90=52.9223 p99=70.7215 p99.9=73.1132 max=74.0923 mean=36.5244 |
| T_release_lag_max_arrival | n=2283 p50=32.8181 p90=38.5779 p99=42.2384 p99.9=45.5731 max=70.7176 mean=26.9421 |
| T_fw_addon | n=22974 p50=9.8381 p90=14.6865 p99=52.9028 p99.9=70.7215 max=74.0923 mean=12.0445 |
| T_fw_addon_sse | n=16086 p50=9.9214 p90=30.4181 p99=53.9543 p99.9=70.9707 max=74.0923 mean=13.2908 |
| T_fw_addon_json | n=6888 p50=9.645 p90=12.7268 p99=16.254 p99.9=20.9073 max=24.5324 mean=9.1341 |
| client_ttft_sse | n=16086 p50=159.5619 p90=162.8507 p99=177.7438 p99.9=183.73 max=201.2774 mean=159.3018 |
| provider_sched_err_last | n=22974 p50=0.0454 p90=0.0872 p99=0.1102 p99.9=0.1289 max=0.1437 mean=0.0469 |
| provider_sched_err_max_per_stream | n=22974 p50=0.0836 p90=0.1253 p99=0.1589 p99.9=0.187 max=0.2391 mean=0.0819 |
| provider_write_max | n=22974 p50=0.017 p90=0.0242 p99=0.0338 p99.9=0.0462 max=0.1324 mean=0.0167 |

- release lag: 2283 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22-078-r3/rv-pbu-lg-1/lg', 'samples': 300, 'busy_max': 8.11599028691865, 'busy_mean': 4.91, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22-078-r3/rv-pbu-lg-2/lg', 'samples': 300, 'busy_max': 5.083638652264522, 'busy_mean': 4.68, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 15.970901427830997, 'busy_p95': 8.07585605957768}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22-078-r3/rv-pbu-lg-1/lg', 'scheduled': 14625, 'recorded': 14625, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22-078-r3/rv-pbu-lg-2/lg', 'scheduled': 14625, 'recorded': 14625, 'interrupted': False}]
- health olg rv-pbu-lg-1: gc_cycles=0 sched_latency_max_ms=0.08192 tcp={'TcpRetransSegs': 9, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 5, 'TcpExtTCPTimeouts': 2, 'TcpOutSegs': 1434562, 'TcpInSegs': 2253725}
- health olg rv-pbu-lg-2: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 5, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 5, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1439212, 'TcpInSegs': 2255401}
- health synthprov rv-pbu-prov-1: gc_cycles=0 sched_latency_max_ms=0.13107200000000002 tcp={'TcpRetransSegs': 31, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 31, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 4628070, 'TcpInSegs': 3281803}
