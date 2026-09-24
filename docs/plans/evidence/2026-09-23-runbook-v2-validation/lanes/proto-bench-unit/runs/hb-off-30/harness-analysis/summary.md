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
- qualified: 11822 (39.41 req/s); expected blocks: 0; errors: 178 (rate 0.014833333333333334)
- error reasons: {'http_403': 178, 'incomplete': 178, 'unjoined': 178, 'disposition_BLOCK': 178, 'stage_dispatch_S': 178}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=12000 p50=0.0874 p90=0.0973 p99=0.11 p99.9=0.1284 max=0.1971 mean=0.0871
- join: {'joined': 11822, 'by_nonce_fallback': 0, 'provider_records': 14779, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=11822 p50=9.5504 p90=12.0928 p99=14.6991 p99.9=17.9434 max=23.5264 mean=9.0001 |
| T_addon_first | n=11822 p50=9.4606 p90=11.9471 p99=14.4321 p99.9=17.8532 max=23.4522 mean=8.9024 |
| T_release_lag_max | n=11822 p50=12.8341 p90=17.0656 p99=20.5557 p99.9=24.4949 max=30.8756 mean=13.0927 |
| T_release_lag_max_arrival | n=11822 p50=12.8341 p90=17.0656 p99=20.5557 p99.9=24.4949 max=30.8756 mean=13.0927 |
| T_fw_addon | n=11822 p50=12.8341 p90=17.0656 p99=20.5557 p99.9=24.4949 max=30.8756 mean=13.0927 |
| T_fw_addon_sse | n=11822 p50=12.8341 p90=17.0656 p99=20.5557 p99.9=24.4949 max=30.8756 mean=13.0927 |
| T_fw_addon_json | n=0 |
| client_ttft_sse | n=11822 p50=159.51 p90=162.0091 p99=164.5049 p99.9=167.9304 max=173.5114 mean=158.9505 |
| provider_sched_err_last | n=11822 p50=0.0474 p90=0.0877 p99=0.1093 p99.9=0.1248 max=0.1591 mean=0.0483 |
| provider_sched_err_max_per_stream | n=11822 p50=0.0963 p90=0.1266 p99=0.1579 p99.9=0.1888 max=0.4012 mean=0.0939 |
| provider_write_max | n=11822 p50=0.0181 p90=0.0246 p99=0.0328 p99.9=0.0434 max=0.063 mean=0.0182 |

- release lag: 11822 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/hb-off-30/rv-pbu-lg-1/lg', 'samples': 300, 'busy_max': 14.624858582595103, 'busy_mean': 5.0, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/hb-off-30/rv-pbu-lg-2/lg', 'samples': 300, 'busy_max': 17.145447717405617, 'busy_mean': 4.72, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 20.419696904519803, 'busy_p95': 6.960858247263834}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/hb-off-30/rv-pbu-lg-1/lg', 'scheduled': 7500, 'recorded': 7500, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/hb-off-30/rv-pbu-lg-2/lg', 'scheduled': 7500, 'recorded': 7500, 'interrupted': False}]
- health olg rv-pbu-lg-1: gc_cycles=0 sched_latency_max_ms=0.08192 tcp={'TcpRetransSegs': 1, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 2, 'TcpOutSegs': 1087604, 'TcpInSegs': 1679451}
- health olg rv-pbu-lg-2: gc_cycles=0 sched_latency_max_ms=0.196608 tcp={'TcpRetransSegs': 1, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 1, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1085401, 'TcpInSegs': 1677527}
- health synthprov rv-pbu-prov-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 2, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 2, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 3383641, 'TcpInSegs': 2789170}
