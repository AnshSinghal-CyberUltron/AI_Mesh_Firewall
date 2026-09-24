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
- qualified: 29278 (97.59 req/s); expected blocks: 0; errors: 722 (rate 0.024066666666666667)
- error reasons: {'incomplete': 722, 'unjoined': 722, 'http_403': 529, 'disposition_BLOCK': 529, 'stage_dispatch_S': 529, 'stage_out_S': 529, 'http_503': 193, 'disposition_missing': 193, 'stage_canon_missing': 193, 'stage_det_missing': 193, 'stage_sem_missing': 193, 'stage_resolve_missing': 193, 'stage_dispatch_missing': 193, 'stage_out_missing': 193, 'stage_audit_missing': 193}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=30000 p50=0.0871 p90=0.099 p99=0.1148 p99.9=0.1363 max=0.2211 mean=0.0864
- join: {'joined': 29278, 'by_nonce_fallback': 0, 'provider_records': 36624, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=29278 p50=9.5561 p90=12.8511 p99=15.9278 p99.9=20.5651 max=26.3023 mean=9.1551 |
| T_addon_first | n=29278 p50=9.5041 p90=13.0269 p99=25.7948 p99.9=33.1515 max=51.9505 mean=9.3173 |
| T_release_lag_max | n=2897 p50=46.2797 p90=53.2381 p99=70.1746 p99.9=89.4875 max=90.7409 mean=36.6478 |
| T_release_lag_max_arrival | n=2897 p50=32.3196 p90=37.9669 p99=42.2952 p99.9=46.0138 max=48.3876 mean=26.5909 |
| T_fw_addon | n=29278 p50=9.8345 p90=15.1018 p99=53.2198 p99.9=70.1053 max=90.7409 mean=12.1325 |
| T_fw_addon_sse | n=20512 p50=9.9304 p90=30.1659 p99=54.075 p99.9=71.3322 max=90.7409 mean=13.3748 |
| T_fw_addon_json | n=8766 p50=9.6168 p90=12.9862 p99=15.8509 p99.9=20.5651 max=26.3023 mean=9.2256 |
| client_ttft_sse | n=20512 p50=159.5027 p90=163.0936 p99=177.2909 p99.9=183.9878 max=202.0496 mean=159.4006 |
| provider_sched_err_last | n=29278 p50=0.0404 p90=0.0868 p99=0.1125 p99.9=0.1345 max=0.1861 mean=0.0437 |
| provider_sched_err_max_per_stream | n=29278 p50=0.0928 p90=0.1356 p99=0.1738 p99.9=0.1979 max=0.2251 mean=0.0882 |
| provider_write_max | n=29278 p50=0.0179 p90=0.0255 p99=0.0346 p99.9=0.0465 max=0.1041 mean=0.0177 |

- release lag: 2897 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22-100/rv-pbu-lg-1/lg', 'samples': 300, 'busy_max': 8.5439813445481, 'busy_mean': 5.27, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22-100/rv-pbu-lg-2/lg', 'samples': 300, 'busy_max': 5.57238133082304, 'busy_mean': 5.04, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 20.9997847956367, 'busy_p95': 8.959246735308934}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22-100/rv-pbu-lg-1/lg', 'scheduled': 18750, 'recorded': 18750, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22-100/rv-pbu-lg-2/lg', 'scheduled': 18750, 'recorded': 18750, 'interrupted': False}]
- health olg rv-pbu-lg-1: gc_cycles=0 sched_latency_max_ms=0.08192 tcp={'TcpRetransSegs': 4, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 4, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1792063, 'TcpInSegs': 2882989}
- health olg rv-pbu-lg-2: gc_cycles=0 sched_latency_max_ms=0.13107200000000002 tcp={'TcpRetransSegs': 5, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 5, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1790256, 'TcpInSegs': 2871562}
- health synthprov rv-pbu-prov-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 32, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 32, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 5907265, 'TcpInSegs': 4184964}
