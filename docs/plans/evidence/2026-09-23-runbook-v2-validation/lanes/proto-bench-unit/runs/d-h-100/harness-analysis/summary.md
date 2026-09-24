# Harness analysis (direct, policy=none) — PASS

| check | result |
|---|---|
| zero_schedule_drops | PASS |
| zero_errors | PASS |
| provider_p99_sched_err_within_tol | PASS |
| loadgen_cpu_max_lt_limit | PASS |
| run_valid | PASS |
| all_joined | PASS |

- offered: 30000 over 300 s = 100.0 req/s (configured 100.0)
- qualified: 30000 (100.0 req/s); expected blocks: 0; errors: 0 (rate 0.0)
- error reasons: {}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=30000 p50=0.0807 p90=0.0917 p99=0.1062 p99.9=0.1876 max=0.3356 mean=0.0796
- join: {'joined': 30000, 'by_nonce_fallback': 0, 'provider_records': 37500, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=30000 p50=0.222 p90=0.3533 p99=0.5943 p99.9=1.1274 max=4.1574 mean=0.2409 |
| T_addon_first | n=30000 p50=0.2268 p90=0.372 p99=0.6133 p99.9=1.091 max=4.1686 mean=0.2482 |
| T_release_lag_max | n=3030 p50=0.3606 p90=0.6425 p99=0.7917 p99.9=1.193 max=2.7426 mean=0.4072 |
| T_release_lag_max_arrival | n=3030 p50=0.3606 p90=0.6425 p99=0.7917 p99.9=1.193 max=2.7426 mean=0.4072 |
| T_fw_addon | n=30000 p50=0.2557 p90=0.4359 p99=0.6909 p99.9=1.1494 max=4.1686 mean=0.2816 |
| T_fw_addon_sse | n=21000 p50=0.2601 p90=0.4303 p99=0.6918 p99.9=1.1395 max=4.1686 mean=0.2826 |
| T_fw_addon_json | n=9000 p50=0.2461 p90=0.4452 p99=0.6864 p99.9=1.1659 max=3.7763 mean=0.2793 |
| client_ttft_sse | n=21000 p50=150.3337 p90=150.4566 p99=150.6905 p99.9=151.1676 max=154.2315 mean=150.3452 |
| provider_sched_err_last | n=30000 p50=0.1152 p90=0.1764 p99=0.2108 p99.9=0.2439 max=0.5289 mean=0.113 |
| provider_sched_err_max_per_stream | n=30000 p50=0.1883 p90=0.2485 p99=0.3033 p99.9=0.3601 max=0.5289 mean=0.1803 |
| provider_write_max | n=30000 p50=0.0166 p90=0.0251 p99=0.0402 p99.9=0.065 max=0.2178 mean=0.0176 |

- release lag: 3030 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/d-h-100/rv-pbu-lg-1/lg', 'samples': 300, 'busy_max': 14.623631582132345, 'busy_mean': 3.59, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/d-h-100/rv-pbu-lg-2/lg', 'samples': 300, 'busy_max': 13.366024692979472, 'busy_mean': 3.24, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 20.039798765457874, 'busy_p95': 7.082562857843233}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/d-h-100/rv-pbu-lg-1/lg', 'scheduled': 18750, 'recorded': 18750, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/d-h-100/rv-pbu-lg-2/lg', 'scheduled': 18750, 'recorded': 18750, 'interrupted': False}]
- health olg rv-pbu-lg-1: gc_cycles=0 sched_latency_max_ms=0.32768 tcp={'TcpRetransSegs': 5, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 5, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 2053406, 'TcpInSegs': 3018091}
- health olg rv-pbu-lg-2: gc_cycles=0 sched_latency_max_ms=0.26214400000000004 tcp={'TcpRetransSegs': 1, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 1, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 2036523, 'TcpInSegs': 3019252}
- health synthprov rv-pbu-prov-1: gc_cycles=0 sched_latency_max_ms=0.26214400000000004 tcp={'TcpRetransSegs': 12, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 15, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 6041374, 'TcpInSegs': 4049625}
