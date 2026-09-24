# Harness analysis (direct, policy={'blocks_total': 0, 'expected_blocks': 0, 'false_positive_blocks': 0, 'other_blocks': 0, 'benign_offered': 71400, 'false_positive_rate': 0.0, 'policy_misses': 0, 'latency_client_total_ms': {}, 'block_rule': {'statuses': [400, 403, 422, 451], 'envelope_re': '(?i)content_filter|content_polic|polic(y|ies)_|blocked|block_|guard|violation|unsafe|threat'}}) — PASS

| check | result |
|---|---|
| zero_schedule_drops | PASS |
| zero_errors | PASS |
| provider_p99_sched_err_within_tol | PASS |
| loadgen_cpu_max_lt_limit | PASS |
| run_valid | PASS |
| all_joined | PASS |

- offered: 71400 over 300 s = 238.0 req/s (configured 238.0)
- qualified: 71400 (238.0 req/s; dispositions ['ALLOW', 'FLAG', 'REDACT'], source auto)
- infra errors (the error-budget gate): 0 (rate 0.0); reasons: {}
- policy stratum (not qualified, not infra errors): expected blocks 0, FALSE-POSITIVE blocks 0 of 71400 benign (FP rate 0.0), other blocks 0, policy misses 0
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=71400 p50=0.0829 p90=0.093 p99=0.105 p99.9=0.1191 max=0.3386 mean=0.0815
- join: {'joined': 71400, 'by_nonce_fallback': 0, 'provider_records': 89250, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=71400 p50=0.1323 p90=0.1625 p99=0.1994 p99.9=0.7499 max=3.8097 mean=0.1368 |
| T_addon_first | n=71400 p50=0.1355 p90=0.1634 p99=0.1998 p99.9=0.7611 max=4.585 mean=0.1399 |
| T_release_lag_max | n=71400 p50=0.1544 p90=0.1786 p99=0.5367 p99.9=2.6318 max=5.7503 mean=0.1702 |
| T_release_lag_max_arrival | n=71400 p50=0.1544 p90=0.1786 p99=0.5367 p99.9=2.6318 max=5.7503 mean=0.1702 |
| T_fw_addon | n=71400 p50=0.1544 p90=0.1786 p99=0.5367 p99.9=2.6318 max=5.7503 mean=0.1702 |
| T_fw_addon_sse | n=49979 p50=0.1557 p90=0.1796 p99=0.6503 p99.9=3.1369 max=5.7503 mean=0.177 |
| T_fw_addon_json | n=21421 p50=0.1505 p90=0.1765 p99=0.2115 p99.9=0.7525 max=3.8097 mean=0.1542 |
| client_ttft_sse | n=49979 p50=150.181 p90=150.224 p99=150.2582 p99.9=150.8263 max=154.6589 mean=150.1843 |
| provider_sched_err_last | n=71400 p50=0.0494 p90=0.0924 p99=0.1159 p99.9=0.1381 max=0.2101 mean=0.0504 |
| provider_sched_err_max_per_stream | n=71400 p50=0.0998 p90=0.143 p99=0.1839 p99.9=0.207 max=0.2417 mean=0.0946 |
| provider_write_max | n=71400 p50=0.0165 p90=0.0225 p99=0.0308 p99.9=0.0414 max=0.4426 mean=0.0163 |

- release lag: 71400 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/d1-238/rv-split-lg-1/lg', 'samples': 300, 'busy_max': 27.16102649115757, 'busy_mean': 15.76, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 429, 'busy_max': 23.2592284918826, 'busy_p95': 12.969256455459977}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/d1-238/rv-split-lg-1/lg', 'scheduled': 89250, 'recorded': 89250, 'interrupted': False}]
- health olg rv-split-lg-1: gc_cycles=0 sched_latency_max_ms=0.32768 tcp={'TcpRetransSegs': 4, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 3, 'TcpExtTCPTimeouts': 2, 'TcpOutSegs': 8709727, 'TcpInSegs': 14391100}
- health synthprov rv-split-prov-1: gc_cycles=0 sched_latency_max_ms=0.917504 tcp={'TcpRetransSegs': 15, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 6, 'TcpExtTCPTimeouts': 9, 'TcpOutSegs': 14400311, 'TcpInSegs': 8620326}
