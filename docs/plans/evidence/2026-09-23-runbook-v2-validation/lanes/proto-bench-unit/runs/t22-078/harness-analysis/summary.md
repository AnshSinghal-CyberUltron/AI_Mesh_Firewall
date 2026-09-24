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
- qualified: 22805 (76.02 req/s); expected blocks: 0; errors: 595 (rate 0.025427350427350427)
- error reasons: {'incomplete': 595, 'unjoined': 595, 'http_403': 413, 'disposition_BLOCK': 413, 'stage_dispatch_S': 413, 'stage_out_S': 413, 'http_503': 182, 'disposition_missing': 182, 'stage_canon_missing': 182, 'stage_det_missing': 182, 'stage_sem_missing': 182, 'stage_resolve_missing': 182, 'stage_dispatch_missing': 182, 'stage_out_missing': 182, 'stage_audit_missing': 182, 'stage_sem_U': 1}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=23400 p50=0.0842 p90=0.0938 p99=0.1048 p99.9=0.1263 max=0.3392 mean=0.0841
- join: {'joined': 22805, 'by_nonce_fallback': 0, 'provider_records': 28536, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=22805 p50=12.8868 p90=16.6023 p99=19.9612 p99.9=25.0293 max=30.0968 mean=13.119 |
| T_addon_first | n=22805 p50=12.8136 p90=16.7372 p99=29.2049 p99.9=37.0808 max=51.1128 mean=13.225 |
| T_release_lag_max | n=2257 p50=51.48 p90=57.0143 p99=73.8271 p99.9=77.7729 max=91.956 mean=41.1391 |
| T_release_lag_max_arrival | n=2257 p50=36.3306 p90=41.0815 p99=45.3692 p99.9=46.9445 max=50.3957 mean=30.5774 |
| T_fw_addon | n=22805 p50=13.0863 p90=18.7831 p99=56.907 p99.9=73.8271 max=91.956 mean=16.1093 |
| T_fw_addon_sse | n=15971 p50=13.1581 p90=34.3154 p99=57.996 p99.9=74.3602 max=91.956 mean=17.3625 |
| T_fw_addon_json | n=6834 p50=12.9479 p90=16.5573 p99=19.8109 p99.9=24.011 max=27.3347 mean=13.1807 |
| client_ttft_sse | n=15971 p50=162.8022 p90=166.8141 p99=181.6946 p99.9=187.4025 max=201.1315 mean=163.2918 |
| provider_sched_err_last | n=22805 p50=0.0472 p90=0.0881 p99=0.1105 p99.9=0.1273 max=0.1795 mean=0.048 |
| provider_sched_err_max_per_stream | n=22805 p50=0.085 p90=0.1254 p99=0.1606 p99.9=0.1959 max=0.5945 mean=0.0827 |
| provider_write_max | n=22805 p50=0.0166 p90=0.0229 p99=0.0324 p99.9=0.0439 max=0.0984 mean=0.0162 |

- release lag: 2257 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/t22-078/rv-pbu-lg-1/lg', 'samples': 300, 'busy_max': 17.238932554052877, 'busy_mean': 4.97, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/t22-078/rv-pbu-lg-2/lg', 'samples': 300, 'busy_max': 14.987984367821937, 'busy_mean': 4.76, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 21.47155692626087, 'busy_p95': 7.959567997478523}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/t22-078/rv-pbu-lg-1/lg', 'scheduled': 14625, 'recorded': 14625, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/t22-078/rv-pbu-lg-2/lg', 'scheduled': 14625, 'recorded': 14625, 'interrupted': False}]
- health olg rv-pbu-lg-1: gc_cycles=0 sched_latency_max_ms=0.08192 tcp={'TcpRetransSegs': 4, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 4, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1421239, 'TcpInSegs': 2234117}
- health olg rv-pbu-lg-2: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 4, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 4, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1438079, 'TcpInSegs': 2244006}
- health synthprov rv-pbu-prov-1: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 14, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 14, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 4595786, 'TcpInSegs': 3271278}
