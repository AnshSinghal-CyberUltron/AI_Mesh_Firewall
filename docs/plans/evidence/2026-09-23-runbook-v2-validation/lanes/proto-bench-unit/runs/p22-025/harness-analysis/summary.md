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
- qualified: 7400 (24.67 req/s); expected blocks: 0; errors: 100 (rate 0.013333333333333334)
- error reasons: {'http_403': 100, 'incomplete': 100, 'unjoined': 100, 'disposition_BLOCK': 100, 'stage_dispatch_S': 100, 'stage_out_S': 100}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=7500 p50=0.0925 p90=0.1083 p99=0.1273 p99.9=0.1677 max=0.1994 mean=0.0933
- join: {'joined': 7400, 'by_nonce_fallback': 0, 'provider_records': 9246, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=7400 p50=9.4635 p90=11.781 p99=14.435 p99.9=17.3709 max=22.362 mean=8.8255 |
| T_addon_first | n=7400 p50=9.4021 p90=12.5763 p99=25.3262 p99.9=30.9155 max=36.3238 mean=8.9739 |
| T_release_lag_max | n=736 p50=46.2917 p90=51.895 p99=70.3591 p99.9=89.036 max=89.036 mean=37.1353 |
| T_release_lag_max_arrival | n=736 p50=31.2466 p90=35.982 p99=38.9692 p99.9=42.4203 max=42.4203 mean=25.9519 |
| T_fw_addon | n=7400 p50=9.7026 p90=13.9047 p99=51.8285 p99.9=70.3591 max=89.036 mean=11.8577 |
| T_fw_addon_sse | n=5174 p50=9.7713 p90=30.6148 p99=52.9001 p99.9=70.699 max=89.036 mean=13.1462 |
| T_fw_addon_json | n=2226 p50=9.5786 p90=11.6705 p99=14.8937 p99.9=17.7063 max=19.1471 mean=8.8628 |
| client_ttft_sse | n=5174 p50=159.4033 p90=162.6972 p99=177.3377 p99.9=181.4174 max=186.3382 mean=159.0706 |
| provider_sched_err_last | n=7400 p50=0.0463 p90=0.0897 p99=0.1184 p99.9=0.2229 max=0.2913 mean=0.048 |
| provider_sched_err_max_per_stream | n=7400 p50=0.0931 p90=0.2379 p99=0.3777 p99.9=0.6378 max=0.6934 mean=0.1112 |
| provider_write_max | n=7400 p50=0.0177 p90=0.0251 p99=0.0378 p99.9=0.0521 max=0.0817 mean=0.0178 |

- release lag: 736 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22-025/rv-pbu-lg-1/lg', 'samples': 300, 'busy_max': 14.185702947112489, 'busy_mean': 4.24, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22-025/rv-pbu-lg-2/lg', 'samples': 300, 'busy_max': 17.562802602287963, 'busy_mean': 4.22, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 18.091125924174978, 'busy_p95': 5.373738791413296}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22-025/rv-pbu-lg-1/lg', 'scheduled': 4688, 'recorded': 4688, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22-025/rv-pbu-lg-2/lg', 'scheduled': 4687, 'recorded': 4687, 'interrupted': False}]
- health olg rv-pbu-lg-1: gc_cycles=0 sched_latency_max_ms=0.08192 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 548741, 'TcpInSegs': 729562}
- health olg rv-pbu-lg-2: gc_cycles=0 sched_latency_max_ms=0.196608 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 550196, 'TcpInSegs': 729168}
- health synthprov rv-pbu-prov-1: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 1, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 1, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1496545, 'TcpInSegs': 1215756}
