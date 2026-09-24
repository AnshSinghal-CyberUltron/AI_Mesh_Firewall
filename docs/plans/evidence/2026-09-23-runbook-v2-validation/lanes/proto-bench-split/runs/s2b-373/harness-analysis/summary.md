# Harness analysis (sut, policy={'blocks_total': 1967, 'expected_blocks': 0, 'false_positive_blocks': 1967, 'other_blocks': 0, 'benign_offered': 111900, 'false_positive_rate': 0.017578194816800716, 'policy_misses': 0, 'latency_client_total_ms': {'policy_block_fp': {'n': 1967, 'min': 3.3132, 'p50': 8.2033, 'p90': 11.8532, 'p99': 16.5377, 'p999': 20.3859, 'max': 22.3908, 'mean': 8.7012}}, 'block_rule': {'statuses': [400, 403, 422, 451], 'envelope_re': '(?i)content_filter|content_polic|polic(y|ies)_|blocked|block_|guard|violation|unsafe|threat'}}) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| infra_error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 111900 over 300 s = 373.0 req/s (configured 373.0)
- qualified: 109933 (366.44 req/s; dispositions ['ALLOW', 'FLAG', 'REDACT'], source auto)
- infra errors (the error-budget gate): 0 (rate 0.0); reasons: {}
- policy stratum (not qualified, not infra errors): expected blocks 0, FALSE-POSITIVE blocks 1967 of 111900 benign (FP rate 0.017578194816800716), other blocks 0, policy misses 0
- policy cohort latency policy_block_fp (client total ms): n=1967 p50=8.2033 p90=11.8532 p99=16.5377 p99.9=20.3859 max=22.3908 mean=8.7012
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=111900 p50=0.0816 p90=0.0907 p99=0.1021 p99.9=0.1191 max=1.1002 mean=0.0807
- join: {'joined': 109933, 'by_nonce_fallback': 0, 'provider_records': 137434, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=109933 p50=8.7624 p90=12.7307 p99=17.1467 p99.9=21.6625 max=41.2949 mean=9.0183 |
| T_addon_first | n=109933 p50=8.7535 p90=12.9191 p99=25.1877 p99.9=33.136 max=56.4721 mean=9.2066 |
| T_release_lag_max | n=109933 p50=47.2799 p90=53.1394 p99=70.7517 p99.9=77.8558 max=114.7594 mean=37.112 |
| T_release_lag_max_arrival | n=109933 p50=31.1435 p90=36.4601 p99=41.5014 p99.9=52.9128 max=68.7866 mean=25.9018 |
| T_fw_addon | n=109933 p50=47.2799 p90=53.1394 p99=70.7517 p99.9=77.8558 max=114.7594 mean=37.112 |
| T_fw_addon_sse | n=76995 p50=48.8597 p90=54.6316 p99=71.5777 p99.9=84.2528 max=114.7594 mean=49.1105 |
| T_fw_addon_json | n=32938 p50=8.8026 p90=12.7779 p99=17.1832 p99.9=21.6014 max=29.5658 mean=9.0646 |
| client_ttft_sse | n=76995 p50=158.7668 p90=163.03 p99=178.1203 p99.9=184.3319 max=206.6315 mean=159.3053 |
| provider_sched_err_last | n=109933 p50=0.0283 p90=0.0862 p99=0.1232 p99.9=0.1582 max=0.4568 mean=0.038 |
| provider_sched_err_max_per_stream | n=109933 p50=0.1217 p90=0.1651 p99=0.2048 p99.9=0.2387 max=0.5459 mean=0.1071 |
| provider_write_max | n=109933 p50=0.0215 p90=0.0297 p99=0.0407 p99.9=0.0524 max=0.1651 mean=0.0211 |

- release lag: 109933 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s2b-373/rv-split-lg-1/lg', 'samples': 300, 'busy_max': 19.67861845735491, 'busy_mean': 15.6, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-b'}]
- provider CPU: {'samples': 431, 'busy_max': 20.10926687150215, 'busy_p95': 19.690628266632903}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s2b-373/rv-split-lg-1/lg', 'scheduled': 139875, 'recorded': 139875, 'interrupted': False}]
- health olg rv-split-lg-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 6, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 6, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 12759316, 'TcpInSegs': 21739398}
- health synthprov rv-split-prov-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 120, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 120, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 22270397, 'TcpInSegs': 17273132}
