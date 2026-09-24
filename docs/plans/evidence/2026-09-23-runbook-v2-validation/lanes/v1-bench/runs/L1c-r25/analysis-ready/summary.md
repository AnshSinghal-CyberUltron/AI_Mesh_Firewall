# Harness analysis (sut, policy={'blocks_total': 0, 'expected_blocks': 0, 'false_positive_blocks': 0, 'other_blocks': 0, 'benign_offered': 7500, 'false_positive_rate': 0.0, 'policy_misses': 0, 'latency_client_total_ms': {}, 'block_rule': {'statuses': [400, 403, 422, 451], 'envelope_re': '(?i)content_filter|content_polic|polic(y|ies)_|blocked|block_|guard|violation|unsafe|threat'}}) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| infra_error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 7500 over 300 s = 25.0 req/s (configured 25.0)
- qualified: 7500 (25.0 req/s; dispositions ['ALLOW', 'FLAG', 'REDACT'], source v1)
- infra errors (the error-budget gate): 0 (rate 0.0); reasons: {}
- policy stratum (not qualified, not infra errors): expected blocks 0, FALSE-POSITIVE blocks 0 of 7500 benign (FP rate 0.0), other blocks 0, policy misses 0
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=7500 p50=0.0865 p90=0.0958 p99=0.1082 p99.9=0.1298 max=0.1397 mean=0.0861
- join: {'joined': 7500, 'by_nonce_fallback': 0, 'provider_records': 9375, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=7500 p50=398.3975 p90=3844.7062 p99=8227.4611 p99.9=10953.2593 max=13339.104 mean=1313.7769 |
| T_addon_first | n=7500 p50=784.5597 p90=1066.7194 p99=1369.3196 p99.9=1623.2148 max=1920.4188 mean=673.9508 |
| T_release_lag_max | n=788 p50=1177.8024 p90=4174.1336 p99=8474.176 p99.9=11336.73 max=11336.73 mean=1658.4871 |
| T_release_lag_max_arrival | n=788 p50=1177.8024 p90=4174.1336 p99=8474.176 p99.9=11336.73 max=11336.73 mean=1658.4871 |
| T_fw_addon | n=7500 p50=848.99 p90=3868.3602 p99=8227.4611 p99.9=10953.2593 max=13339.104 mean=1481.1781 |
| T_fw_addon_sse | n=5250 p50=1151.7936 p90=4720.5515 p99=8670.634 p99.9=11380.5554 max=13339.104 mean=2023.591 |
| T_fw_addon_json | n=2250 p50=204.832 p90=306.3285 p99=504.9108 p99.9=620.5444 max=806.287 mean=215.548 |
| client_ttft_sse | n=5250 p50=1005.7547 p90=1271.1341 p99=1571.4019 p99.9=1799.0354 max=2070.4873 mean=1020.4602 |
| provider_sched_err_last | n=7500 p50=0.0525 p90=0.0898 p99=0.1062 p99.9=0.1228 max=0.1389 mean=0.0514 |
| provider_sched_err_max_per_stream | n=7500 p50=0.0743 p90=0.113 p99=0.1351 p99.9=0.1765 max=0.6846 mean=0.0735 |
| provider_write_max | n=7500 p50=0.0136 p90=0.0199 p99=0.0294 p99.9=0.0441 max=0.0613 mean=0.0144 |

- release lag: 788 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/v1-bench/runs/L1c-r25/lg-v1ready', 'samples': 300, 'busy_max': 7.823398412228411, 'busy_mean': 4.67, 'machine': 'c4-standard-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 412, 'busy_max': 24.998363937734915, 'busy_p95': 5.088655572841083}
- completeness: [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/v1-bench/runs/L1c-r25/lg-v1ready', 'scheduled': 9375, 'recorded': 9375, 'interrupted': False}]
- health olg L1c-r25: gc_cycles=0 sched_latency_max_ms=0.13107200000000002 tcp=None
- health synthprov rv-v1-prov-1: gc_cycles=0 sched_latency_max_ms=0.13107200000000002 tcp={'TcpRetransSegs': 3956, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 3956, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1505696, 'TcpInSegs': 1494290}
