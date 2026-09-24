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
- qualified: 22953 (76.51 req/s); expected blocks: 0; errors: 447 (rate 0.019102564102564102)
- error reasons: {'incomplete': 447, 'unjoined': 447, 'http_403': 429, 'disposition_BLOCK': 429, 'stage_dispatch_S': 429, 'stage_out_S': 429, 'http_503': 18, 'disposition_missing': 18, 'stage_canon_missing': 18, 'stage_det_missing': 18, 'stage_sem_missing': 18, 'stage_resolve_missing': 18, 'stage_dispatch_missing': 18, 'stage_out_missing': 18, 'stage_audit_missing': 18, 'stage_sem_U': 1}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=23400 p50=0.0841 p90=0.0938 p99=0.1048 p99.9=0.1231 max=0.2777 mean=0.0839
- join: {'joined': 22953, 'by_nonce_fallback': 0, 'provider_records': 28722, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=22953 p50=9.5586 p90=12.3912 p99=15.7279 p99.9=20.3126 max=44.4698 mean=9.0208 |
| T_addon_first | n=22953 p50=9.5273 p90=12.7967 p99=25.8139 p99.9=33.5136 max=50.1619 mean=9.2037 |
| T_release_lag_max | n=2372 p50=46.2998 p90=52.8391 p99=70.6751 p99.9=74.3207 max=87.9793 mean=37.6173 |
| T_release_lag_max_arrival | n=2372 p50=32.9678 p90=38.6748 p99=42.6163 p99.9=52.6101 max=57.1932 mean=27.554 |
| T_fw_addon | n=22953 p50=9.839 p90=14.9448 p99=52.9385 p99.9=70.7274 max=87.9793 mean=12.2449 |
| T_fw_addon_sse | n=16066 p50=9.9257 p90=35.1176 p99=53.964 p99.9=71.0131 max=87.9793 mean=13.5934 |
| T_fw_addon_json | n=6887 p50=9.6372 p90=12.8088 p99=15.7717 p99.9=19.7838 max=24.7655 mean=9.0992 |
| client_ttft_sse | n=16066 p50=159.5269 p90=162.8592 p99=179.1014 p99.9=183.8415 max=200.1947 mean=159.2966 |
| provider_sched_err_last | n=22953 p50=0.0463 p90=0.0886 p99=0.11 p99.9=0.1256 max=0.18 mean=0.0476 |
| provider_sched_err_max_per_stream | n=22953 p50=0.0856 p90=0.1253 p99=0.1609 p99.9=0.1957 max=0.674 mean=0.0827 |
| provider_write_max | n=22953 p50=0.0163 p90=0.0225 p99=0.0323 p99.9=0.0452 max=0.1584 mean=0.016 |

- release lag: 2372 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22-078-r2/rv-pbu-lg-1/lg', 'samples': 300, 'busy_max': 15.727446508159105, 'busy_mean': 4.88, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22-078-r2/rv-pbu-lg-2/lg', 'samples': 300, 'busy_max': 17.64851605030081, 'busy_mean': 4.68, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 21.801019759015862, 'busy_p95': 7.853075018044297}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22-078-r2/rv-pbu-lg-1/lg', 'scheduled': 14625, 'recorded': 14625, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22-078-r2/rv-pbu-lg-2/lg', 'scheduled': 14625, 'recorded': 14625, 'interrupted': False}]
- health olg rv-pbu-lg-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 2, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 2, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1430851, 'TcpInSegs': 2254160}
- health olg rv-pbu-lg-2: gc_cycles=0 sched_latency_max_ms=0.08192 tcp={'TcpRetransSegs': 8, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 3, 'TcpExtTCPTimeouts': 6, 'TcpOutSegs': 1433307, 'TcpInSegs': 2252992}
- health synthprov rv-pbu-prov-1: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 35, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 35, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 4625727, 'TcpInSegs': 3267424}
