# Harness analysis (sut, policy=none) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| error_rate_le_budget | FAIL |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 12000 over 300 s = 40.0 req/s (configured 40.0)
- qualified: 11819 (39.4 req/s); expected blocks: 0; errors: 181 (rate 0.015083333333333334)
- error reasons: {'http_403': 181, 'incomplete': 181, 'unjoined': 181, 'disposition_BLOCK': 181, 'stage_dispatch_S': 181, 'stage_out_S': 181}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=12000 p50=0.0867 p90=0.0968 p99=0.1091 p99.9=0.1388 max=0.2333 mean=0.0871
- join: {'joined': 11819, 'by_nonce_fallback': 0, 'provider_records': 14775, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=11819 p50=9.6238 p90=12.3539 p99=14.9943 p99.9=18.5161 max=20.6955 mean=9.1077 |
| T_addon_first | n=11819 p50=9.5503 p90=12.8661 p99=39.5084 p99.9=43.6196 max=70.3945 mean=9.5461 |
| T_release_lag_max | n=11819 p50=69.3677 p90=73.5868 p99=101.2335 p99.9=128.8239 max=159.1405 mean=68.6068 |
| T_release_lag_max_arrival | n=11819 p50=43.4324 p90=47.669 p99=50.8397 p99.9=54.7207 max=56.9593 mean=43.5919 |
| T_fw_addon | n=11819 p50=69.3677 p90=73.5868 p99=101.2335 p99.9=128.8239 max=159.1405 mean=68.6068 |
| T_fw_addon_sse | n=11819 p50=69.3677 p90=73.5868 p99=101.2335 p99.9=128.8239 max=159.1405 mean=68.6068 |
| T_fw_addon_json | n=0 |
| client_ttft_sse | n=11819 p50=159.6044 p90=162.9244 p99=189.5218 p99.9=193.6719 max=220.4016 mean=159.5939 |
| provider_sched_err_last | n=11819 p50=0.0472 p90=0.0873 p99=0.109 p99.9=0.1257 max=0.1582 mean=0.0481 |
| provider_sched_err_max_per_stream | n=11819 p50=0.096 p90=0.1272 p99=0.1628 p99.9=0.1936 max=0.5456 mean=0.0939 |
| provider_write_max | n=11819 p50=0.018 p90=0.0251 p99=0.0326 p99.9=0.0495 max=0.1141 mean=0.0183 |

- release lag: 11819 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/hb-on-30/rv-pbu-lg-1/lg', 'samples': 300, 'busy_max': 16.840900392071966, 'busy_mean': 4.92, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/hb-on-30/rv-pbu-lg-2/lg', 'samples': 300, 'busy_max': 16.507488067957475, 'busy_mean': 4.97, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 20.441194411745222, 'busy_p95': 6.906715442045385}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/hb-on-30/rv-pbu-lg-1/lg', 'scheduled': 7500, 'recorded': 7500, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/hb-on-30/rv-pbu-lg-2/lg', 'scheduled': 7500, 'recorded': 7500, 'interrupted': False}]
- health olg rv-pbu-lg-1: gc_cycles=0 sched_latency_max_ms=0.229376 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1446647, 'TcpInSegs': 1651094}
- health olg rv-pbu-lg-2: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 2, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 2, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1447319, 'TcpInSegs': 1650204}
- health synthprov rv-pbu-prov-1: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 3, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 3, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 3383234, 'TcpInSegs': 2802186}
