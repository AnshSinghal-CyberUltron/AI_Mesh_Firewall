# Harness analysis (sut, policy=none) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| error_rate_le_budget | FAIL |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 18900 over 300 s = 63.0 req/s (configured 63.0)
- qualified: 18407 (61.36 req/s); expected blocks: 0; errors: 493 (rate 0.026084656084656085)
- error reasons: {'incomplete': 493, 'unjoined': 493, 'http_403': 483, 'disposition_BLOCK': 483, 'stage_dispatch_S': 483, 'stage_out_S': 483, 'http_503': 10, 'disposition_missing': 10, 'stage_canon_missing': 10, 'stage_det_missing': 10, 'stage_sem_missing': 10, 'stage_resolve_missing': 10, 'stage_dispatch_missing': 10, 'stage_out_missing': 10, 'stage_audit_missing': 10}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=18900 p50=0.0856 p90=0.0952 p99=0.1059 p99.9=0.1251 max=0.2457 mean=0.0854
- join: {'joined': 18407, 'by_nonce_fallback': 0, 'provider_records': 23012, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=18407 p50=14.3224 p90=15.5301 p99=21.4923 p99.9=25.3887 max=30.6234 mean=14.5824 |
| T_addon_first | n=18407 p50=14.2761 p90=15.5603 p99=32.9295 p99.9=35.3423 max=54.6885 mean=14.686 |
| T_release_lag_max | n=1858 p50=53.9473 p90=60.1096 p99=74.9388 p99.9=93.8149 max=94.7085 mean=44.3033 |
| T_release_lag_max_arrival | n=1858 p50=41.6354 p90=44.045 p99=48.1078 p99.9=51.2631 max=58.4835 mean=34.2329 |
| T_fw_addon | n=18407 p50=14.4466 p90=20.0674 p99=60.1429 p99.9=74.9388 max=94.7085 mean=17.8536 |
| T_fw_addon_sse | n=12886 p50=14.4958 p90=52.8607 p99=72.5143 p99.9=75.1463 max=94.7085 mean=19.2554 |
| T_fw_addon_json | n=5521 p50=14.3592 p90=15.415 p99=20.9537 p99.9=25.3753 max=29.7639 mean=14.5819 |
| client_ttft_sse | n=12886 p50=164.2851 p90=165.6481 p99=183.7464 p99.9=185.9458 max=204.709 mean=164.7763 |
| provider_sched_err_last | n=18407 p50=0.0439 p90=0.0875 p99=0.1113 p99.9=0.1295 max=0.1519 mean=0.0457 |
| provider_sched_err_max_per_stream | n=18407 p50=0.0936 p90=0.1335 p99=0.1707 p99.9=0.2035 max=0.296 mean=0.0887 |
| provider_write_max | n=18407 p50=0.0204 p90=0.0263 p99=0.038 p99.9=0.0504 max=0.1434 mean=0.0209 |

- release lag: 1858 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/w22-063/rv-pbu-lg-1/lg', 'samples': 300, 'busy_max': 9.16632406356712, 'busy_mean': 6.04, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/w22-063/rv-pbu-lg-2/lg', 'samples': 300, 'busy_max': 6.024856834443593, 'busy_mean': 5.76, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 10.24843135737884, 'busy_p95': 9.118385516763572}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/w22-063/rv-pbu-lg-1/lg', 'scheduled': 11813, 'recorded': 11813, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/w22-063/rv-pbu-lg-2/lg', 'scheduled': 11812, 'recorded': 11812, 'interrupted': False}]
- health olg rv-pbu-lg-1: gc_cycles=0 sched_latency_max_ms=0.13107200000000002 tcp={'TcpRetransSegs': 3, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 3, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1842957, 'TcpInSegs': 3187848}
- health olg rv-pbu-lg-2: gc_cycles=0 sched_latency_max_ms=0.08192 tcp={'TcpRetransSegs': 6, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 6, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1843645, 'TcpInSegs': 3187907}
- health synthprov rv-pbu-prov-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 16, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 16, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 6520308, 'TcpInSegs': 4132363}
