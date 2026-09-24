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
- schedule drops (>5.0 ms): 0; lateness ms: n=12000 p50=0.0853 p90=0.0955 p99=0.1084 p99.9=0.123 max=0.2373 mean=0.0852
- join: {'joined': 11822, 'by_nonce_fallback': 0, 'provider_records': 14781, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=11822 p50=9.5244 p90=12.23 p99=14.7745 p99.9=18.3806 max=23.0856 mean=8.9988 |
| T_addon_first | n=11822 p50=9.4537 p90=12.6831 p99=17.9713 p99.9=23.9212 max=29.6676 mean=9.0377 |
| T_release_lag_max | n=11822 p50=29.3125 p90=33.3939 p99=41.2014 p99.9=45.9645 max=53.2198 mean=28.9204 |
| T_release_lag_max_arrival | n=11822 p50=22.9599 p90=27.1831 p99=30.6491 p99.9=33.8545 max=43.9612 mean=23.2077 |
| T_fw_addon | n=11822 p50=29.3125 p90=33.3939 p99=41.2014 p99.9=45.9645 max=53.2198 mean=28.9204 |
| T_fw_addon_sse | n=11822 p50=29.3125 p90=33.3939 p99=41.2014 p99.9=45.9645 max=53.2198 mean=28.9204 |
| T_fw_addon_json | n=0 |
| client_ttft_sse | n=11822 p50=159.514 p90=162.7305 p99=168.0175 p99.9=173.9908 max=179.7236 mean=159.0864 |
| provider_sched_err_last | n=11822 p50=0.048 p90=0.0881 p99=0.1088 p99.9=0.1259 max=0.1723 mean=0.0487 |
| provider_sched_err_max_per_stream | n=11822 p50=0.0964 p90=0.1261 p99=0.1515 p99.9=0.1794 max=0.2034 mean=0.0936 |
| provider_write_max | n=11822 p50=0.0179 p90=0.0243 p99=0.0317 p99.9=0.0423 max=0.0574 mean=0.0179 |

- release lag: 11822 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/hb-on-10/rv-pbu-lg-1/lg', 'samples': 300, 'busy_max': 8.081604357138705, 'busy_mean': 4.78, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/hb-on-10/rv-pbu-lg-2/lg', 'samples': 300, 'busy_max': 4.825525623480075, 'busy_mean': 4.5, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 420, 'busy_max': 20.45425154791016, 'busy_p95': 6.96574933313917}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/hb-on-10/rv-pbu-lg-1/lg', 'scheduled': 7500, 'recorded': 7500, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/hb-on-10/rv-pbu-lg-2/lg', 'scheduled': 7500, 'recorded': 7500, 'interrupted': False}]
- health olg rv-pbu-lg-1: gc_cycles=0 sched_latency_max_ms=0.196608 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1089516, 'TcpInSegs': 1651212}
- health olg rv-pbu-lg-2: gc_cycles=0 sched_latency_max_ms=0.13107200000000002 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1091507, 'TcpInSegs': 1650247}
- health synthprov rv-pbu-prov-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 2, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 2, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 3383859, 'TcpInSegs': 2788994}
