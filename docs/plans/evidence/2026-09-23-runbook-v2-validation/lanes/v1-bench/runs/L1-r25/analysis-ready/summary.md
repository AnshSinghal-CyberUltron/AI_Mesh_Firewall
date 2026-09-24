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
- schedule drops (>5.0 ms): 0; lateness ms: n=7500 p50=0.0853 p90=0.0953 p99=0.108 p99.9=0.1312 max=0.1564 mean=0.0861
- join: {'joined': 7500, 'by_nonce_fallback': 0, 'provider_records': 9375, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=7500 p50=480.48 p90=3767.1034 p99=9033.134 p99.9=11192.2763 max=11919.5945 mean=1375.4923 |
| T_addon_first | n=7500 p50=791.091 p90=1075.782 p99=1360.9537 p99.9=1615.0106 max=1751.267 mean=676.8847 |
| T_release_lag_max | n=745 p50=1214.3876 p90=3874.8326 p99=9118.1257 p99.9=10871.3371 max=10871.3371 mean=1683.5859 |
| T_release_lag_max_arrival | n=745 p50=1214.3876 p90=3874.8326 p99=9118.1257 p99.9=10871.3371 max=10871.3371 mean=1683.5859 |
| T_fw_addon | n=7500 p50=873.7688 p90=3788.6024 p99=9033.134 p99.9=11192.2763 max=11919.5945 mean=1522.4377 |
| T_fw_addon_sse | n=5250 p50=1220.7336 p90=4682.1873 p99=9341.9557 p99.9=11249.3001 max=11919.5945 mean=2081.7585 |
| T_fw_addon_json | n=2250 p50=207.0572 p90=309.9194 p99=529.5654 p99.9=622.5234 max=794.6124 mean=217.3559 |
| client_ttft_sse | n=5250 p50=1012.843 p90=1278.5126 p99=1551.3312 p99.9=1844.3781 max=1901.2704 mean=1023.8761 |
| provider_sched_err_last | n=7500 p50=0.0516 p90=0.09 p99=0.1065 p99.9=0.1226 max=0.1423 mean=0.0511 |
| provider_sched_err_max_per_stream | n=7500 p50=0.0744 p90=0.1134 p99=0.1352 p99.9=0.18 max=0.2085 mean=0.0733 |
| provider_write_max | n=7500 p50=0.0132 p90=0.0201 p99=0.0303 p99.9=0.0441 max=0.0981 mean=0.0142 |

- release lag: 745 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/v1-bench/runs/L1-r25/lg-v1ready', 'samples': 300, 'busy_max': 8.062854777388251, 'busy_mean': 4.83, 'machine': 'c4-standard-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 411, 'busy_max': 5.523844432247382, 'busy_p95': 5.119570229969817}
- completeness: [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/v1-bench/runs/L1-r25/lg-v1ready', 'scheduled': 9375, 'recorded': 9375, 'interrupted': False}]
- health olg L1-r25: gc_cycles=0 sched_latency_max_ms=0.098304 tcp=None
- health synthprov rv-v1-prov-1: gc_cycles=0 sched_latency_max_ms=0.16384 tcp={'TcpRetransSegs': 4009, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 4009, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1505596, 'TcpInSegs': 1490749}
