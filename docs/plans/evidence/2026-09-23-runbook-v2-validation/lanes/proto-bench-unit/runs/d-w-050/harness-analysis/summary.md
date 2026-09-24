# Harness analysis (direct, policy={'blocks_total': 0, 'expected_blocks': 0, 'false_positive_blocks': 0, 'other_blocks': 0, 'benign_offered': 15000, 'false_positive_rate': 0.0, 'policy_misses': 0, 'latency_client_total_ms': {}, 'block_rule': {'statuses': [400, 403, 422, 451], 'envelope_re': '(?i)content_filter|content_polic|polic(y|ies)_|blocked|block_|guard|violation|unsafe|threat'}}) — PASS

| check | result |
|---|---|
| zero_schedule_drops | PASS |
| zero_errors | PASS |
| provider_p99_sched_err_within_tol | PASS |
| loadgen_cpu_max_lt_limit | PASS |
| run_valid | PASS |
| all_joined | PASS |

- offered: 15000 over 300 s = 50.0 req/s (configured 50.0)
- qualified: 15000 (50.0 req/s; dispositions ['ALLOW', 'FLAG', 'REDACT'], source auto)
- infra errors (the error-budget gate): 0 (rate 0.0); reasons: {}
- policy stratum (not qualified, not infra errors): expected blocks 0, FALSE-POSITIVE blocks 0 of 15000 benign (FP rate 0.0), other blocks 0, policy misses 0
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=15000 p50=0.0847 p90=0.0977 p99=0.1352 p99.9=0.1786 max=0.2685 mean=0.0849
- join: {'joined': 15000, 'by_nonce_fallback': 0, 'provider_records': 18750, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=15000 p50=0.2159 p90=0.4233 p99=0.6676 p99.9=0.8009 max=9.1873 mean=0.2528 |
| T_addon_first | n=15000 p50=0.2274 p90=0.4442 p99=0.6802 p99.9=0.8073 max=9.1873 mean=0.2675 |
| T_release_lag_max | n=1518 p50=0.4714 p90=0.7661 p99=1.0497 p99.9=5.4125 max=9.1905 mean=0.5078 |
| T_release_lag_max_arrival | n=1518 p50=0.4714 p90=0.7661 p99=1.0497 p99.9=5.4125 max=9.1905 mean=0.5078 |
| T_fw_addon | n=15000 p50=0.2636 p90=0.5271 p99=0.7801 p99.9=1.0827 max=9.1905 mean=0.3103 |
| T_fw_addon_sse | n=10500 p50=0.2727 p90=0.5251 p99=0.7961 p99.9=1.1225 max=9.1905 mean=0.31 |
| T_fw_addon_json | n=4500 p50=0.2483 p90=0.5286 p99=0.7303 p99.9=0.8347 max=9.1873 mean=0.3111 |
| client_ttft_sse | n=10500 p50=150.3651 p90=150.5988 p99=150.8434 p99.9=150.9979 max=151.2843 mean=150.3946 |
| provider_sched_err_last | n=15000 p50=0.1509 p90=0.2253 p99=0.3647 p99.9=0.4714 max=0.6289 mean=0.1483 |
| provider_sched_err_max_per_stream | n=15000 p50=0.4112 p90=0.5282 p99=0.6398 p99.9=0.7259 max=0.7499 mean=0.3601 |
| provider_write_max | n=15000 p50=0.0248 p90=0.0411 p99=0.0645 p99.9=0.1025 max=0.4082 mean=0.0276 |

- release lag: 1518 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/d-w-050/rv-pbu-lg-1/lg', 'samples': 300, 'busy_max': 15.275760025725127, 'busy_mean': 4.42, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/d-w-050/rv-pbu-lg-2/lg', 'samples': 300, 'busy_max': 16.96113902581329, 'busy_mean': 4.14, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 18.142393763324062, 'busy_p95': 7.054552297769135}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/d-w-050/rv-pbu-lg-1/lg', 'scheduled': 9375, 'recorded': 9375, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/d-w-050/rv-pbu-lg-2/lg', 'scheduled': 9375, 'recorded': 9375, 'interrupted': False}]
- health olg rv-pbu-lg-1: gc_cycles=0 sched_latency_max_ms=0.65536 tcp={'TcpRetransSegs': 9, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 4, 'TcpExtTCPTimeouts': 4, 'TcpOutSegs': 2078279, 'TcpInSegs': 2654570}
- health olg rv-pbu-lg-2: gc_cycles=0 sched_latency_max_ms=0.65536 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 2090449, 'TcpInSegs': 2653287}
- health synthprov rv-pbu-prov-1: gc_cycles=0 sched_latency_max_ms=0.458752 tcp={'TcpRetransSegs': 1, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 1, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 5310927, 'TcpInSegs': 4125372}
