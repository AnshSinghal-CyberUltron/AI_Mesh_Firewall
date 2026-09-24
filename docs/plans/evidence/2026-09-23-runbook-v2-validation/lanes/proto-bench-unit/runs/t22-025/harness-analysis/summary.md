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
- qualified: 7403 (24.68 req/s); expected blocks: 0; errors: 97 (rate 0.012933333333333333)
- error reasons: {'http_403': 97, 'incomplete': 97, 'unjoined': 97, 'disposition_BLOCK': 97, 'stage_dispatch_S': 97, 'stage_out_S': 97}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=7500 p50=0.0899 p90=0.1051 p99=0.1272 p99.9=0.2033 max=0.3047 mean=0.0915
- join: {'joined': 7403, 'by_nonce_fallback': 0, 'provider_records': 9251, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=7403 p50=12.7655 p90=15.6754 p99=18.3962 p99.9=22.3503 max=25.0872 mean=12.8025 |
| T_addon_first | n=7403 p50=12.7538 p90=16.4684 p99=31.6668 p99.9=36.8522 max=57.5012 mean=13.032 |
| T_release_lag_max | n=714 p50=51.0961 p90=55.6938 p99=72.7933 p99.9=92.2851 max=92.2851 mean=40.509 |
| T_release_lag_max_arrival | n=714 p50=35.7947 p90=39.5298 p99=43.1319 p99.9=48.6329 max=48.6329 mean=29.6968 |
| T_fw_addon | n=7403 p50=13.0164 p90=17.8493 p99=55.6873 p99.9=72.7933 max=92.2851 mean=15.7996 |
| T_fw_addon_sse | n=5175 p50=13.1064 p90=33.874 p99=56.6734 p99.9=72.8622 max=92.2851 mean=17.0617 |
| T_fw_addon_json | n=2228 p50=12.8335 p90=16.0244 p99=18.4638 p99.9=22.3345 max=25.0872 mean=12.8679 |
| client_ttft_sse | n=5175 p50=162.7642 p90=166.615 p99=182.7668 p99.9=187.2503 max=207.53 mean=163.1503 |
| provider_sched_err_last | n=7403 p50=0.0459 p90=0.0886 p99=0.1119 p99.9=0.1916 max=0.2391 mean=0.0478 |
| provider_sched_err_max_per_stream | n=7403 p50=0.0868 p90=0.1451 p99=0.2814 p99.9=0.3661 max=0.4755 mean=0.092 |
| provider_write_max | n=7403 p50=0.0171 p90=0.0253 p99=0.0363 p99.9=0.0479 max=0.0931 mean=0.0172 |

- release lag: 714 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/t22-025/rv-pbu-lg-1/lg', 'samples': 300, 'busy_max': 17.652852132677708, 'busy_mean': 4.25, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/t22-025/rv-pbu-lg-2/lg', 'samples': 300, 'busy_max': 17.22195174896617, 'busy_mean': 4.05, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 19.218138114593774, 'busy_p95': 5.255564039392158}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/t22-025/rv-pbu-lg-1/lg', 'scheduled': 4688, 'recorded': 4688, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/t22-025/rv-pbu-lg-2/lg', 'scheduled': 4687, 'recorded': 4687, 'interrupted': False}]
- health olg rv-pbu-lg-1: gc_cycles=0 sched_latency_max_ms=0.196608 tcp={'TcpRetransSegs': 5, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 6, 'TcpOutSegs': 555431, 'TcpInSegs': 730653}
- health olg rv-pbu-lg-2: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 552494, 'TcpInSegs': 728608}
- health synthprov rv-pbu-prov-1: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 5, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 5, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1496900, 'TcpInSegs': 1263097}
