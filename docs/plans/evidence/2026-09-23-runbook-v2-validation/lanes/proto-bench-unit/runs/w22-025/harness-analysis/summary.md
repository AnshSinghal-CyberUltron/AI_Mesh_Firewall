# Harness analysis (sut, policy=none) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| error_rate_le_budget | FAIL |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 7500 over 300 s = 25.0 req/s (configured 25.0)
- qualified: 7338 (24.46 req/s); expected blocks: 0; errors: 162 (rate 0.0216)
- error reasons: {'http_403': 162, 'incomplete': 162, 'unjoined': 162, 'disposition_BLOCK': 162, 'stage_dispatch_S': 162, 'stage_out_S': 162}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=7500 p50=0.0833 p90=0.0953 p99=0.108 p99.9=0.1261 max=0.1848 mean=0.0774
- join: {'joined': 7338, 'by_nonce_fallback': 0, 'provider_records': 9166, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=7338 p50=14.259 p90=14.8752 p99=18.9713 p99.9=21.9683 max=37.6966 mean=14.3237 |
| T_addon_first | n=7338 p50=14.2022 p90=14.9705 p99=33.5914 p99.9=34.7938 max=54.3435 mean=14.5689 |
| T_release_lag_max | n=737 p50=53.9322 p90=57.0208 p99=74.3068 p99.9=113.9174 max=113.9174 mean=42.9248 |
| T_release_lag_max_arrival | n=737 p50=40.2946 p90=41.9446 p99=45.3668 p99.9=47.9576 max=47.9576 mean=32.3364 |
| T_fw_addon | n=7338 p50=14.3879 p90=17.0288 p99=57.0208 p99.9=74.3068 max=113.9174 mean=17.5577 |
| T_fw_addon_sse | n=5134 p50=14.3875 p90=34.4709 p99=72.9114 p99.9=74.5988 max=113.9174 mean=18.889 |
| T_fw_addon_json | n=2204 p50=14.3895 p90=14.9809 p99=18.7219 p99.9=22.0752 max=26.8731 mean=14.4566 |
| client_ttft_sse | n=5134 p50=164.158 p90=165.0078 p99=183.9119 p99.9=184.8866 max=204.3673 mean=164.6648 |
| provider_sched_err_last | n=7338 p50=0.0313 p90=0.097 p99=0.2137 p99.9=0.329 max=0.5904 mean=0.0439 |
| provider_sched_err_max_per_stream | n=7338 p50=0.2755 p90=0.451 p99=0.7437 p99.9=0.981 max=1.1141 mean=0.2464 |
| provider_write_max | n=7338 p50=0.0242 p90=0.0344 p99=0.0499 p99.9=0.0692 max=0.1358 mean=0.0256 |

- release lag: 737 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/w22-025/rv-pbu-lg-1/lg', 'samples': 300, 'busy_max': 15.235227498193115, 'busy_mean': 4.86, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/w22-025/rv-pbu-lg-2/lg', 'samples': 300, 'busy_max': 16.748111047397696, 'busy_mean': 4.61, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 18.98544494791071, 'busy_p95': 5.524865126799683}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/w22-025/rv-pbu-lg-1/lg', 'scheduled': 4688, 'recorded': 4688, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/w22-025/rv-pbu-lg-2/lg', 'scheduled': 4687, 'recorded': 4687, 'interrupted': False}]
- health olg rv-pbu-lg-1: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 1, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 1, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 778958, 'TcpInSegs': 1269600}
- health olg rv-pbu-lg-2: gc_cycles=0 sched_latency_max_ms=0.196608 tcp={'TcpRetransSegs': 3, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 3, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 764235, 'TcpInSegs': 1268094}
- health synthprov rv-pbu-prov-1: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 5, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 6, 'TcpOutSegs': 2594508, 'TcpInSegs': 1495895}
