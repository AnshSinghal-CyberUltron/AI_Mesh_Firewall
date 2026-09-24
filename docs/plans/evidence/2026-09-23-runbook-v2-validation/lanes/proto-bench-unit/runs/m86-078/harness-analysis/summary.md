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
- qualified: 21994 (73.31 req/s); expected blocks: 0; errors: 1406 (rate 0.060085470085470084)
- error reasons: {'incomplete': 1406, 'unjoined': 1406, 'http_503': 1086, 'disposition_missing': 1086, 'stage_canon_missing': 1086, 'stage_det_missing': 1086, 'stage_sem_missing': 1086, 'stage_resolve_missing': 1086, 'stage_dispatch_missing': 1086, 'stage_out_missing': 1086, 'stage_audit_missing': 1086, 'http_403': 320, 'disposition_BLOCK': 320, 'stage_dispatch_S': 320, 'stage_out_S': 320}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=23400 p50=0.0833 p90=0.0932 p99=0.1044 p99.9=0.1284 max=0.5849 mean=0.0834
- join: {'joined': 21994, 'by_nonce_fallback': 0, 'provider_records': 27577, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=21994 p50=14.7222 p90=17.3248 p99=22.7804 p99.9=27.5908 max=31.2515 mean=13.2395 |
| T_addon_first | n=21994 p50=14.6849 p90=17.5523 p99=28.9809 p99.9=40.9103 max=55.394 mean=13.4256 |
| T_release_lag_max | n=2151 p50=48.8144 p90=57.7194 p99=75.8776 p99.9=83.6551 max=100.4634 mean=41.1043 |
| T_release_lag_max_arrival | n=2151 p50=34.9309 p90=42.7967 p99=48.6115 p99.9=52.221 max=58.5991 mean=30.784 |
| T_fw_addon | n=21994 p50=15.0037 p90=22.1259 p99=57.6026 p99.9=75.8776 max=100.4634 mean=16.2418 |
| T_fw_addon_sse | n=15396 p50=15.1039 p90=35.4623 p99=60.996 p99.9=76.2015 max=100.4634 mean=17.5033 |
| T_fw_addon_json | n=6598 p50=14.7847 p90=17.376 p99=22.8462 p99.9=27.5277 max=31.2515 mean=13.2982 |
| client_ttft_sse | n=15396 p50=164.695 p90=167.7448 p99=184.4008 p99.9=191.4394 max=205.4883 mean=163.5273 |
| provider_sched_err_last | n=21994 p50=0.0456 p90=0.088 p99=0.1096 p99.9=0.1231 max=0.165 mean=0.0474 |
| provider_sched_err_max_per_stream | n=21994 p50=0.0838 p90=0.1243 p99=0.159 p99.9=0.1915 max=0.2457 mean=0.0815 |
| provider_write_max | n=21994 p50=0.0163 p90=0.0226 p99=0.0328 p99.9=0.0461 max=0.0835 mean=0.0161 |

- release lag: 2151 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/m86-078/rv-pbu-lg-1/lg', 'samples': 300, 'busy_max': 7.8263363737160585, 'busy_mean': 4.68, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/m86-078/rv-pbu-lg-2/lg', 'samples': 300, 'busy_max': 4.918498146001927, 'busy_mean': 4.54, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 17.494036015610227, 'busy_p95': 7.771362391375847}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/m86-078/rv-pbu-lg-1/lg', 'scheduled': 14625, 'recorded': 14625, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/m86-078/rv-pbu-lg-2/lg', 'scheduled': 14625, 'recorded': 14625, 'interrupted': False}]
- health olg rv-pbu-lg-1: gc_cycles=0 sched_latency_max_ms=0.08192 tcp={'TcpRetransSegs': 5, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 5, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1387460, 'TcpInSegs': 2155063}
- health olg rv-pbu-lg-2: gc_cycles=0 sched_latency_max_ms=0.08192 tcp={'TcpRetransSegs': 7, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 7, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1391077, 'TcpInSegs': 2168853}
- health synthprov rv-pbu-prov-1: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 7, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 7, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 4435497, 'TcpInSegs': 3185894}
