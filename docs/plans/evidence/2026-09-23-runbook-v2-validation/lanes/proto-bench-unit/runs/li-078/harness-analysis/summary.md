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
- qualified: 22978 (76.59 req/s); expected blocks: 0; errors: 422 (rate 0.018034188034188034)
- error reasons: {'http_403': 422, 'incomplete': 422, 'unjoined': 422, 'disposition_BLOCK': 422, 'stage_dispatch_S': 422, 'stage_out_S': 422}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=23400 p50=0.0846 p90=0.0942 p99=0.1058 p99.9=0.1258 max=0.2197 mean=0.0846
- join: {'joined': 22978, 'by_nonce_fallback': 0, 'provider_records': 28754, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=22978 p50=9.7504 p90=12.0384 p99=14.6383 p99.9=15.7469 max=25.0426 mean=9.1064 |
| T_addon_first | n=22978 p50=9.7012 p90=12.2209 p99=25.7267 p99.9=32.0079 max=53.3906 mean=9.2605 |
| T_release_lag_max | n=22978 p50=46.3494 p90=51.9029 p99=70.8396 p99.9=74.848 max=93.9193 mean=36.7707 |
| T_release_lag_max_arrival | n=22978 p50=26.9425 p90=31.7184 p99=34.8299 p99.9=40.1189 max=46.1267 mean=23.2449 |
| T_fw_addon | n=22978 p50=46.3494 p90=51.9029 p99=70.8396 p99.9=74.848 max=93.9193 mean=36.7707 |
| T_fw_addon_sse | n=16084 p50=49.4388 p90=53.51 p99=71.1647 p99.9=86.051 max=93.9193 mean=48.5959 |
| T_fw_addon_json | n=6894 p50=9.7926 p90=12.1904 p99=14.6992 p99.9=15.6519 max=25.0426 mean=9.182 |
| client_ttft_sse | n=16084 p50=159.7163 p90=162.2656 p99=178.9121 p99.9=183.181 max=203.4722 mean=159.3422 |
| provider_sched_err_last | n=22978 p50=0.0466 p90=0.0882 p99=0.109 p99.9=0.1236 max=0.1764 mean=0.0478 |
| provider_sched_err_max_per_stream | n=22978 p50=0.0835 p90=0.123 p99=0.1541 p99.9=0.1917 max=0.3582 mean=0.081 |
| provider_write_max | n=22978 p50=0.0163 p90=0.0228 p99=0.0314 p99.9=0.0406 max=0.1345 mean=0.0161 |

- release lag: 22978 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/li-078/rv-pbu-lg-3/lg', 'samples': 300, 'busy_max': 15.692713250876334, 'busy_mean': 4.98, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/li-078/rv-pbu-lg-4/lg', 'samples': 300, 'busy_max': 17.605016914338634, 'busy_mean': 4.88, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 20.47167723046114, 'busy_p95': 7.626881751926174}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/li-078/rv-pbu-lg-3/lg', 'scheduled': 14625, 'recorded': 14625, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/li-078/rv-pbu-lg-4/lg', 'scheduled': 14625, 'recorded': 14625, 'interrupted': False}]
- health olg rv-pbu-lg-3: gc_cycles=0 sched_latency_max_ms=0.08192 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1417694, 'TcpInSegs': 2255823}
- health olg rv-pbu-lg-4: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1405291, 'TcpInSegs': 2255347}
- health synthprov rv-pbu-prov-2: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 4629590, 'TcpInSegs': 3290855}
