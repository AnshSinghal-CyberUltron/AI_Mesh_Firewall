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
- qualified: 14764 (49.21 req/s); expected blocks: 0; errors: 236 (rate 0.015733333333333332)
- error reasons: {'incomplete': 236, 'unjoined': 236, 'http_403': 197, 'disposition_BLOCK': 197, 'stage_dispatch_S': 197, 'stage_out_S': 197, 'http_503': 39, 'disposition_missing': 39, 'stage_canon_missing': 39, 'stage_det_missing': 39, 'stage_sem_missing': 39, 'stage_resolve_missing': 39, 'stage_dispatch_missing': 39, 'stage_out_missing': 39, 'stage_audit_missing': 39}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=15000 p50=0.0845 p90=0.0941 p99=0.1069 p99.9=0.1231 max=0.2192 mean=0.0845
- join: {'joined': 14764, 'by_nonce_fallback': 0, 'provider_records': 18466, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=14764 p50=14.4098 p90=17.0551 p99=22.0221 p99.9=25.7047 max=28.9372 mean=13.0239 |
| T_addon_first | n=14764 p50=14.3407 p90=17.3696 p99=27.0824 p99.9=40.8703 max=56.0815 mean=13.1373 |
| T_release_lag_max | n=1425 p50=48.5374 p90=56.7278 p99=75.44 p99.9=80.9847 max=95.6726 mean=41.0672 |
| T_release_lag_max_arrival | n=1425 p50=33.9327 p90=41.6386 p99=47.1067 p99.9=49.1278 max=50.5305 mean=30.1187 |
| T_fw_addon | n=14764 p50=14.6801 p90=21.6509 p99=56.6797 p99.9=75.44 max=95.6726 mean=15.942 |
| T_fw_addon_sse | n=10330 p50=14.78 p90=35.1527 p99=60.6059 p99.9=75.7347 max=95.6726 mean=17.1744 |
| T_fw_addon_json | n=4434 p50=14.4619 p90=17.1293 p99=22.0459 p99.9=24.9787 max=28.9372 mean=13.0709 |
| client_ttft_sse | n=10330 p50=164.3503 p90=167.5517 p99=178.8207 p99.9=191.2517 max=206.1308 mean=163.2099 |
| provider_sched_err_last | n=14764 p50=0.0409 p90=0.0881 p99=0.1133 p99.9=0.1368 max=0.2817 mean=0.0441 |
| provider_sched_err_max_per_stream | n=14764 p50=0.0917 p90=0.134 p99=0.1844 p99.9=0.2914 max=0.3724 mean=0.0876 |
| provider_write_max | n=14764 p50=0.0178 p90=0.0248 p99=0.0359 p99.9=0.0477 max=0.1112 mean=0.0175 |

- release lag: 1425 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/m86-050/rv-pbu-lg-1/lg', 'samples': 300, 'busy_max': 7.5858350391835305, 'busy_mean': 4.46, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/m86-050/rv-pbu-lg-2/lg', 'samples': 300, 'busy_max': 4.494047340201568, 'busy_mean': 4.27, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 16.494617692900526, 'busy_p95': 6.314018739279992}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/m86-050/rv-pbu-lg-1/lg', 'scheduled': 9375, 'recorded': 9375, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/m86-050/rv-pbu-lg-2/lg', 'scheduled': 9375, 'recorded': 9375, 'interrupted': False}]
- health olg rv-pbu-lg-1: gc_cycles=0 sched_latency_max_ms=0.196608 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 948257, 'TcpInSegs': 1445193}
- health olg rv-pbu-lg-2: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 1, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 1, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 943004, 'TcpInSegs': 1447078}
- health synthprov rv-pbu-prov-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 15, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 9, 'TcpExtTCPTimeouts': 6, 'TcpOutSegs': 2968698, 'TcpInSegs': 2236739}
