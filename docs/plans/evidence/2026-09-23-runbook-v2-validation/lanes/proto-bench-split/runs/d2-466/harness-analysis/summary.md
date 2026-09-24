# Harness analysis (direct, policy={'blocks_total': 0, 'expected_blocks': 0, 'false_positive_blocks': 0, 'other_blocks': 0, 'benign_offered': 139800, 'false_positive_rate': 0.0, 'policy_misses': 0, 'latency_client_total_ms': {}, 'block_rule': {'statuses': [400, 403, 422, 451], 'envelope_re': '(?i)content_filter|content_polic|polic(y|ies)_|blocked|block_|guard|violation|unsafe|threat'}}) — PASS

| check | result |
|---|---|
| zero_schedule_drops | PASS |
| zero_errors | PASS |
| provider_p99_sched_err_within_tol | PASS |
| loadgen_cpu_max_lt_limit | PASS |
| run_valid | PASS |
| all_joined | PASS |

- offered: 139800 over 300 s = 466.0 req/s (configured 466.0)
- qualified: 139800 (466.0 req/s; dispositions ['ALLOW', 'FLAG', 'REDACT'], source auto)
- infra errors (the error-budget gate): 0 (rate 0.0); reasons: {}
- policy stratum (not qualified, not infra errors): expected blocks 0, FALSE-POSITIVE blocks 0 of 139800 benign (FP rate 0.0), other blocks 0, policy misses 0
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=139800 p50=0.0858 p90=0.1014 p99=0.1249 p99.9=0.1512 max=0.3584 mean=0.0847
- join: {'joined': 139800, 'by_nonce_fallback': 0, 'provider_records': 174750, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=139800 p50=0.136 p90=0.1707 p99=0.2078 p99.9=0.338 max=4.1567 mean=0.1392 |
| T_addon_first | n=139800 p50=0.1409 p90=0.1722 p99=0.2093 p99.9=0.3556 max=2.9503 mean=0.1436 |
| T_release_lag_max | n=139800 p50=0.165 p90=0.1918 p99=0.4081 p99.9=1.6151 max=4.3123 mean=0.174 |
| T_release_lag_max_arrival | n=139800 p50=0.165 p90=0.1918 p99=0.4081 p99.9=1.6151 max=4.3123 mean=0.174 |
| T_fw_addon | n=139800 p50=0.165 p90=0.1918 p99=0.4081 p99.9=1.6151 max=4.3123 mean=0.174 |
| T_fw_addon_sse | n=97859 p50=0.1674 p90=0.1939 p99=0.4781 p99.9=2.0079 max=4.3123 mean=0.1801 |
| T_fw_addon_json | n=41941 p50=0.1576 p90=0.1864 p99=0.2203 p99.9=0.3642 max=2.83 mean=0.1599 |
| client_ttft_sse | n=97859 p50=150.1699 p90=150.2248 p99=150.2715 p99.9=150.3991 max=153.0644 mean=150.1751 |
| provider_sched_err_last | n=139800 p50=0.0316 p90=0.0849 p99=0.1222 p99.9=0.1566 max=0.2537 mean=0.0388 |
| provider_sched_err_max_per_stream | n=139800 p50=0.1213 p90=0.1655 p99=0.2021 p99.9=0.2314 max=0.3657 mean=0.1067 |
| provider_write_max | n=139800 p50=0.0184 p90=0.0264 p99=0.0339 p99.9=0.0447 max=0.1799 mean=0.0185 |

- release lag: 139800 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/d2-466/rv-split-lg-1/lg', 'samples': 300, 'busy_max': 30.860879170114664, 'busy_mean': 27.58, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 429, 'busy_max': 21.190111049941805, 'busy_p95': 20.83204086464734}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/d2-466/rv-split-lg-1/lg', 'scheduled': 174750, 'recorded': 174750, 'interrupted': False}]
- health olg rv-split-lg-1: gc_cycles=0 sched_latency_max_ms=0.393216 tcp={'TcpRetransSegs': 28, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 4, 'TcpExtTCPTimeouts': 26, 'TcpOutSegs': 16230595, 'TcpInSegs': 28187430}
- health synthprov rv-split-prov-1: gc_cycles=0 sched_latency_max_ms=1.572864 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 28206151, 'TcpInSegs': 16049073}
