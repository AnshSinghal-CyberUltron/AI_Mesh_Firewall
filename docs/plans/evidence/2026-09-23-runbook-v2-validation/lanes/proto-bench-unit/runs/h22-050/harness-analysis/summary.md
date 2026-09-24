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
- qualified: 14765 (49.22 req/s); expected blocks: 0; errors: 235 (rate 0.015666666666666666)
- error reasons: {'incomplete': 235, 'unjoined': 235, 'http_403': 232, 'disposition_BLOCK': 232, 'stage_dispatch_S': 232, 'stage_out_S': 232, 'http_503': 3, 'disposition_missing': 3, 'stage_canon_missing': 3, 'stage_det_missing': 3, 'stage_sem_missing': 3, 'stage_resolve_missing': 3, 'stage_dispatch_missing': 3, 'stage_out_missing': 3, 'stage_audit_missing': 3, 'stage_sem_U': 3}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=15000 p50=0.0899 p90=0.1036 p99=0.1186 p99.9=0.1335 max=0.2023 mean=0.0907
- join: {'joined': 14765, 'by_nonce_fallback': 0, 'provider_records': 18470, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=14765 p50=9.5613 p90=12.0311 p99=14.9459 p99.9=19.5367 max=162.912 mean=8.9595 |
| T_addon_first | n=14765 p50=9.507 p90=12.7022 p99=25.8784 p99.9=33.1272 max=115.45 mean=9.1178 |
| T_release_lag_max | n=1437 p50=46.1389 p90=52.9599 p99=71.0603 p99.9=222.9255 max=223.0174 mean=36.9045 |
| T_release_lag_max_arrival | n=1437 p50=30.9089 p90=36.7464 p99=40.0519 p99.9=222.9255 max=223.0174 mean=26.1155 |
| T_fw_addon | n=14765 p50=9.799 p90=14.0636 p99=52.9584 p99.9=72.9609 max=223.0174 mean=11.951 |
| T_fw_addon_sse | n=10336 p50=9.8574 p90=29.8263 p99=54.5423 p99.9=73.6088 max=223.0174 mean=13.2136 |
| T_fw_addon_json | n=4429 p50=9.6192 p90=12.2755 p99=14.933 p99.9=18.217 max=22.0217 mean=9.0045 |
| client_ttft_sse | n=10336 p50=159.5084 p90=162.7826 p99=177.2144 p99.9=183.6803 max=265.4557 mean=159.2101 |
| provider_sched_err_last | n=14765 p50=0.0399 p90=0.0879 p99=0.1184 p99.9=0.2103 max=0.5378 mean=0.0438 |
| provider_sched_err_max_per_stream | n=14765 p50=0.0991 p90=0.1998 p99=0.3254 p99.9=0.568 max=1.0231 mean=0.106 |
| provider_write_max | n=14765 p50=0.0182 p90=0.0259 p99=0.037 p99.9=0.0505 max=0.0701 mean=0.0183 |

- release lag: 1437 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/h22-050/rv-pbu-lg-1/lg', 'samples': 300, 'busy_max': 7.6067225763680195, 'busy_mean': 4.55, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/h22-050/rv-pbu-lg-2/lg', 'samples': 300, 'busy_max': 7.569500406899787, 'busy_mean': 4.36, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 17.557122834018358, 'busy_p95': 6.563367673148212}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/h22-050/rv-pbu-lg-1/lg', 'scheduled': 9375, 'recorded': 9375, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/h22-050/rv-pbu-lg-2/lg', 'scheduled': 9375, 'recorded': 9375, 'interrupted': False}]
- health olg rv-pbu-lg-1: gc_cycles=0 sched_latency_max_ms=0.08192 tcp={'TcpRetransSegs': 1, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 1, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 947674, 'TcpInSegs': 1445784}
- health olg rv-pbu-lg-2: gc_cycles=0 sched_latency_max_ms=0.229376 tcp={'TcpRetransSegs': 2, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 2, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 955394, 'TcpInSegs': 1445008}
- health synthprov rv-pbu-prov-1: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 16, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 16, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 2967612, 'TcpInSegs': 2227550}
