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
- error reasons: {'http_403': 178, 'incomplete': 178, 'unjoined': 178, 'disposition_BLOCK': 178, 'stage_dispatch_S': 178, 'stage_out_S': 178}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=12000 p50=0.0872 p90=0.097 p99=0.1085 p99.9=0.1242 max=0.3094 mean=0.0877
- join: {'joined': 11822, 'by_nonce_fallback': 0, 'provider_records': 14779, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=11822 p50=9.5656 p90=12.3109 p99=14.7324 p99.9=18.677 max=23.9564 mean=9.0417 |
| T_addon_first | n=11822 p50=9.4938 p90=12.7656 p99=29.2968 p99.9=33.754 max=66.3509 mean=9.2952 |
| T_release_lag_max | n=11822 p50=49.3286 p90=53.478 p99=71.061 p99.9=85.9928 max=93.4984 mean=48.7811 |
| T_release_lag_max_arrival | n=11822 p50=33.0649 p90=37.3233 p99=40.4969 p99.9=43.5877 max=47.7131 mean=33.2839 |
| T_fw_addon | n=11822 p50=49.3286 p90=53.478 p99=71.061 p99.9=85.9928 max=93.4984 mean=48.7811 |
| T_fw_addon_sse | n=11822 p50=49.3286 p90=53.478 p99=71.061 p99.9=85.9928 max=93.4984 mean=48.7811 |
| T_fw_addon_json | n=0 |
| client_ttft_sse | n=11822 p50=159.539 p90=162.8076 p99=179.3772 p99.9=183.8168 max=216.3822 mean=159.3428 |
| provider_sched_err_last | n=11822 p50=0.0464 p90=0.0883 p99=0.1086 p99.9=0.1255 max=0.1422 mean=0.0477 |
| provider_sched_err_max_per_stream | n=11822 p50=0.0978 p90=0.1268 p99=0.1587 p99.9=0.1994 max=0.915 mean=0.0946 |
| provider_write_max | n=11822 p50=0.018 p90=0.0247 p99=0.033 p99.9=0.0506 max=0.8295 mean=0.0182 |

- release lag: 11822 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/hb-on-20/rv-pbu-lg-1/lg', 'samples': 300, 'busy_max': 18.79953058809796, 'busy_mean': 5.12, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/hb-on-20/rv-pbu-lg-2/lg', 'samples': 300, 'busy_max': 17.858869369863616, 'busy_mean': 4.68, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 20.755858651420176, 'busy_p95': 6.854034881493709}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/hb-on-20/rv-pbu-lg-1/lg', 'scheduled': 7500, 'recorded': 7500, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/hb-on-20/rv-pbu-lg-2/lg', 'scheduled': 7500, 'recorded': 7500, 'interrupted': False}]
- health olg rv-pbu-lg-1: gc_cycles=0 sched_latency_max_ms=0.08192 tcp={'TcpRetransSegs': 1, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 1, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1127179, 'TcpInSegs': 1650910}
- health olg rv-pbu-lg-2: gc_cycles=0 sched_latency_max_ms=0.08192 tcp={'TcpRetransSegs': 1, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 1, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1128819, 'TcpInSegs': 1650777}
- health synthprov rv-pbu-prov-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 5, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 4, 'TcpExtTCPTimeouts': 1, 'TcpOutSegs': 3383859, 'TcpInSegs': 2806611}
