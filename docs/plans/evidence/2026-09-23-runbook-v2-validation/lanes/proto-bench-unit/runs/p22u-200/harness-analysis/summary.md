# Harness analysis (sut, policy=none) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| error_rate_le_budget | FAIL |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 60000 over 300 s = 200.0 req/s (configured 200.0)
- qualified: 58917 (196.39 req/s); expected blocks: 0; errors: 1083 (rate 0.01805)
- error reasons: {'http_403': 1083, 'incomplete': 1083, 'unjoined': 1083, 'disposition_BLOCK': 1083, 'stage_dispatch_S': 1083, 'stage_out_S': 1083}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=60000 p50=0.0846 p90=0.0945 p99=0.1067 p99.9=0.1312 max=0.3569 mean=0.085
- join: {'joined': 58917, 'by_nonce_fallback': 0, 'provider_records': 73687, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=58917 p50=10.7553 p90=15.1714 p99=20.8489 p99.9=26.8261 max=36.8184 mean=10.7723 |
| T_addon_first | n=58917 p50=10.7096 p90=15.3585 p99=27.2144 p99.9=35.9929 max=70.023 mean=10.9312 |
| T_release_lag_max | n=5853 p50=48.0002 p90=56.3561 p99=72.9635 p99.9=91.8131 max=103.8607 mean=39.2633 |
| T_release_lag_max_arrival | n=5853 p50=35.983 p90=42.9263 p99=48.434 p99.9=53.5213 max=63.2501 mean=30.2036 |
| T_fw_addon | n=58917 p50=11.1771 p90=19.1814 p99=56.3498 p99.9=72.9635 max=103.8607 mean=13.9581 |
| T_fw_addon_sse | n=41249 p50=11.376 p90=34.0361 p99=58.5972 p99.9=74.3072 max=103.8607 mean=15.2929 |
| T_fw_addon_json | n=17668 p50=10.8063 p90=15.2857 p99=20.7511 p99.9=26.6192 max=36.8184 mean=10.842 |
| client_ttft_sse | n=41249 p50=160.7177 p90=165.4506 p99=179.7266 p99.9=186.8424 max=220.0623 mean=161.0102 |
| provider_sched_err_last | n=58917 p50=0.0339 p90=0.0867 p99=0.1159 p99.9=0.1402 max=0.2074 mean=0.0406 |
| provider_sched_err_max_per_stream | n=58917 p50=0.1051 p90=0.1459 p99=0.1857 p99.9=0.2095 max=0.2375 mean=0.0954 |
| provider_write_max | n=58917 p50=0.0185 p90=0.0257 p99=0.0357 p99.9=0.0481 max=0.0712 mean=0.0183 |

- release lag: 5853 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22u-200/rv-pbu-lg-1/lg', 'samples': 300, 'busy_max': 21.095448197014633, 'busy_mean': 7.72, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22u-200/rv-pbu-lg-2/lg', 'samples': 300, 'busy_max': 20.190998753904232, 'busy_mean': 7.54, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 25.761183838591485, 'busy_p95': 13.303069873397277}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22u-200/rv-pbu-lg-1/lg', 'scheduled': 37500, 'recorded': 37500, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22u-200/rv-pbu-lg-2/lg', 'scheduled': 37500, 'recorded': 37500, 'interrupted': False}]
- health olg rv-pbu-lg-1: gc_cycles=0 sched_latency_max_ms=0.08192 tcp={'TcpRetransSegs': 21, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 21, 'TcpExtTCPTimeouts': 1, 'TcpOutSegs': 3595992, 'TcpInSegs': 5777309}
- health olg rv-pbu-lg-2: gc_cycles=0 sched_latency_max_ms=0.08192 tcp={'TcpRetransSegs': 16, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 16, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 3593591, 'TcpInSegs': 5781796}
- health synthprov rv-pbu-prov-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 129, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 129, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 11867863, 'TcpInSegs': 8032165}
