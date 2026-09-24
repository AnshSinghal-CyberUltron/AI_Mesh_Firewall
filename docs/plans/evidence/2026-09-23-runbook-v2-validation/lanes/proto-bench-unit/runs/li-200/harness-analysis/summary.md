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
- qualified: 58946 (196.49 req/s); expected blocks: 0; errors: 1054 (rate 0.017566666666666668)
- error reasons: {'http_403': 1054, 'incomplete': 1054, 'unjoined': 1054, 'disposition_BLOCK': 1054, 'stage_dispatch_S': 1054, 'stage_out_S': 1054}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=60000 p50=0.0839 p90=0.0929 p99=0.1038 p99.9=0.1227 max=0.3407 mean=0.0838
- join: {'joined': 58946, 'by_nonce_fallback': 0, 'provider_records': 73724, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=58946 p50=10.808 p90=14.3087 p99=18.0378 p99.9=20.0828 max=57.511 mean=10.5997 |
| T_addon_first | n=58946 p50=10.765 p90=14.4719 p99=26.9919 p99.9=34.9851 max=67.8258 mean=10.7535 |
| T_release_lag_max | n=58946 p50=47.6767 p90=54.218 p99=72.5565 p99.9=79.1114 max=110.9836 mean=38.2771 |
| T_release_lag_max_arrival | n=58946 p50=28.7193 p90=34.2139 p99=39.3834 p99.9=67.5491 max=77.804 mean=25.145 |
| T_fw_addon | n=58946 p50=47.6767 p90=54.218 p99=72.5565 p99.9=79.1114 max=110.9836 mean=38.2771 |
| T_fw_addon_sse | n=41273 p50=50.5106 p90=55.7029 p99=73.1828 p99.9=87.1294 max=110.9836 mean=50.1024 |
| T_fw_addon_json | n=17673 p50=10.8431 p90=14.4005 p99=18.0384 p99.9=20.109 max=45.6514 mean=10.6608 |
| client_ttft_sse | n=41273 p50=160.7765 p90=164.5592 p99=179.5284 p99.9=185.8235 max=217.8661 mean=160.8342 |
| provider_sched_err_last | n=58946 p50=0.035 p90=0.0861 p99=0.116 p99.9=0.1387 max=0.2014 mean=0.0409 |
| provider_sched_err_max_per_stream | n=58946 p50=0.1037 p90=0.1452 p99=0.1845 p99.9=0.2207 max=0.5421 mean=0.0948 |
| provider_write_max | n=58946 p50=0.0188 p90=0.0267 p99=0.0361 p99.9=0.0458 max=0.0644 mean=0.0187 |

- release lag: 58946 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/li-200/rv-pbu-lg-3/lg', 'samples': 300, 'busy_max': 21.172367887685372, 'busy_mean': 7.92, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/li-200/rv-pbu-lg-4/lg', 'samples': 300, 'busy_max': 19.35286697650902, 'busy_mean': 7.73, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 23.50252713721852, 'busy_p95': 13.083484435000825}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/li-200/rv-pbu-lg-3/lg', 'scheduled': 37500, 'recorded': 37500, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/li-200/rv-pbu-lg-4/lg', 'scheduled': 37500, 'recorded': 37500, 'interrupted': False}]
- health olg rv-pbu-lg-3: gc_cycles=0 sched_latency_max_ms=0.16384 tcp={'TcpRetransSegs': 1, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 1, 'TcpOutSegs': 3456370, 'TcpInSegs': 5783115}
- health olg rv-pbu-lg-4: gc_cycles=0 sched_latency_max_ms=0.393216 tcp={'TcpRetransSegs': 6, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 1, 'TcpExtTCPTimeouts': 6, 'TcpOutSegs': 3462840, 'TcpInSegs': 5782834}
- health synthprov rv-pbu-prov-2: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 43, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 38, 'TcpExtTCPTimeouts': 6, 'TcpOutSegs': 11874842, 'TcpInSegs': 8068332}
