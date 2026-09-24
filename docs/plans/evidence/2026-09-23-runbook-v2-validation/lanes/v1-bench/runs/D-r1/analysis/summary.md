# Harness analysis (direct, policy=none) — PASS

| check | result |
|---|---|
| zero_schedule_drops | PASS |
| zero_errors | PASS |
| provider_p99_sched_err_within_tol | PASS |
| loadgen_cpu_max_lt_limit | PASS |
| run_valid | PASS |
| all_joined | PASS |

- offered: 300 over 300 s = 1.0 req/s (configured 1.0)
- qualified: 300 (1.0 req/s); expected blocks: 0; errors: 0 (rate 0.0)
- error reasons: {}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=300 p50=0.1303 p90=0.1731 p99=0.246 p99.9=0.3116 max=0.3116 mean=0.135
- join: {'joined': 300, 'by_nonce_fallback': 0, 'provider_records': 375, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=300 p50=0.44 p90=0.6411 p99=0.7858 p99.9=1.0369 max=1.0369 mean=0.4537 |
| T_addon_first | n=300 p50=0.4677 p90=0.6687 p99=0.7858 p99.9=1.0369 max=1.0369 mean=0.4695 |
| T_release_lag_max | n=27 p50=0.6869 p90=0.8121 p99=1.0548 p99.9=1.0548 max=1.0548 mean=0.6504 |
| T_release_lag_max_arrival | n=27 p50=0.6869 p90=0.8121 p99=1.0548 p99.9=1.0548 max=1.0548 mean=0.6504 |
| T_fw_addon | n=300 p50=0.5041 p90=0.6942 p99=0.9155 p99.9=1.0548 max=1.0548 mean=0.514 |
| T_fw_addon_sse | n=210 p50=0.4956 p90=0.6869 p99=0.9155 p99.9=1.0548 max=1.0548 mean=0.5051 |
| T_fw_addon_json | n=90 p50=0.5262 p90=0.7268 p99=1.0369 p99.9=1.0369 max=1.0369 mean=0.5347 |
| client_ttft_sse | n=210 p50=150.5258 p90=150.7585 p99=150.9323 p99.9=150.9695 max=150.9695 mean=150.5373 |
| provider_sched_err_last | n=300 p50=0.0577 p90=0.1886 p99=0.3293 p99.9=0.4971 max=0.4971 mean=0.0794 |
| provider_sched_err_max_per_stream | n=300 p50=0.2407 p90=0.3775 p99=0.4292 p99.9=0.4971 max=0.4971 mean=0.2234 |
| provider_write_max | n=300 p50=0.0271 p90=0.0405 p99=0.0608 p99.9=0.0706 max=0.0706 mean=0.0281 |

- release lag: 27 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/v1-bench/runs/D-r1/rv-v1-lg-1/lg', 'samples': 300, 'busy_max': 11.67088009087528, 'busy_mean': 3.7, 'machine': 'c4-standard-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 401, 'busy_max': 21.891047989813494, 'busy_p95': 9.615905060010776}
- completeness: [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/v1-bench/runs/D-r1/rv-v1-lg-1/lg', 'scheduled': 375, 'recorded': 375, 'interrupted': False}]
- health olg rv-v1-lg-1: gc_cycles=0 sched_latency_max_ms=0.196608 tcp={'TcpRetransSegs': 7, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 1, 'TcpExtTCPTimeouts': 6, 'TcpOutSegs': 60485, 'TcpInSegs': 62728}
- health synthprov rv-v1-prov-1: gc_cycles=0 sched_latency_max_ms=0.16384 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 61886, 'TcpInSegs': 59350}
