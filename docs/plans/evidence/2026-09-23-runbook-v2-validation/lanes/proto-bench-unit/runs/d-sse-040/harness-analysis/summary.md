# Harness analysis (direct, policy=none) — PASS

| check | result |
|---|---|
| zero_schedule_drops | PASS |
| zero_errors | PASS |
| provider_p99_sched_err_within_tol | PASS |
| loadgen_cpu_max_lt_limit | PASS |
| run_valid | PASS |
| all_joined | PASS |

- offered: 12000 over 300 s = 40.0 req/s (configured 40.0)
- qualified: 12000 (40.0 req/s); expected blocks: 0; errors: 0 (rate 0.0)
- error reasons: {}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=12000 p50=0.0877 p90=0.0999 p99=0.1169 p99.9=0.1504 max=0.3071 mean=0.0887
- join: {'joined': 12000, 'by_nonce_fallback': 0, 'provider_records': 15000, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=12000 p50=0.1544 p90=0.1915 p99=0.3096 p99.9=1.5593 max=4.026 mean=0.163 |
| T_addon_first | n=12000 p50=0.1628 p90=0.2028 p99=0.3216 p99.9=1.5643 max=4.0337 mean=0.1721 |
| T_release_lag_max | n=12000 p50=0.2262 p90=0.2646 p99=0.5778 p99.9=1.5747 max=4.0357 mean=0.2423 |
| T_release_lag_max_arrival | n=12000 p50=0.2262 p90=0.2646 p99=0.5778 p99.9=1.5747 max=4.0357 mean=0.2423 |
| T_fw_addon | n=12000 p50=0.2262 p90=0.2646 p99=0.5778 p99.9=1.5747 max=4.0357 mean=0.2423 |
| T_fw_addon_sse | n=12000 p50=0.2262 p90=0.2646 p99=0.5778 p99.9=1.5747 max=4.0357 mean=0.2423 |
| T_fw_addon_json | n=0 |
| client_ttft_sse | n=12000 p50=150.2079 p90=150.249 p99=150.38 p99.9=151.6107 max=154.1183 mean=150.215 |
| provider_sched_err_last | n=12000 p50=0.0776 p90=0.1215 p99=0.1507 p99.9=0.178 max=0.2318 mean=0.0754 |
| provider_sched_err_max_per_stream | n=12000 p50=0.152 p90=0.1847 p99=0.2138 p99.9=0.2449 max=0.267 mean=0.1515 |
| provider_write_max | n=12000 p50=0.0172 p90=0.0255 p99=0.0344 p99.9=0.0429 max=0.1062 mean=0.0182 |

- release lag: 12000 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/d-sse-040/rv-pbu-lg-1/lg', 'samples': 300, 'busy_max': 16.758470277040992, 'busy_mean': 4.46, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/d-sse-040/rv-pbu-lg-2/lg', 'samples': 300, 'busy_max': 17.897208070957728, 'busy_mean': 4.03, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 18.08916150353449, 'busy_p95': 6.748427381648203}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/d-sse-040/rv-pbu-lg-1/lg', 'scheduled': 7500, 'recorded': 7500, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/d-sse-040/rv-pbu-lg-2/lg', 'scheduled': 7500, 'recorded': 7500, 'interrupted': False}]
- health olg rv-pbu-lg-1: gc_cycles=0 sched_latency_max_ms=0.196608 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1519795, 'TcpInSegs': 1719451}
- health olg rv-pbu-lg-2: gc_cycles=0 sched_latency_max_ms=0.16384 tcp={'TcpRetransSegs': 2, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 2, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1517157, 'TcpInSegs': 1717064}
- health synthprov rv-pbu-prov-1: gc_cycles=0 sched_latency_max_ms=0.16384 tcp={'TcpRetransSegs': 5, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 5, 'TcpOutSegs': 3435145, 'TcpInSegs': 3021921}
