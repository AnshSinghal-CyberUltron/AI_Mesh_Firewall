# Harness analysis (sut, policy={'blocks_total': 996, 'expected_blocks': 0, 'false_positive_blocks': 996, 'other_blocks': 0, 'benign_offered': 57300, 'false_positive_rate': 0.017382198952879582, 'policy_misses': 0, 'latency_client_total_ms': {'policy_block_fp': {'n': 996, 'min': 3.3459, 'p50': 7.4351, 'p90': 9.8199, 'p99': 12.5181, 'p999': 16.8979, 'max': 16.8979, 'mean': 7.3921}}, 'block_rule': {'statuses': [400, 403, 422, 451], 'envelope_re': '(?i)content_filter|content_polic|polic(y|ies)_|blocked|block_|guard|violation|unsafe|threat'}}) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| infra_error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 57300 over 300 s = 191.0 req/s (configured 191.0)
- qualified: 56304 (187.68 req/s; dispositions ['ALLOW', 'FLAG', 'REDACT'], source auto)
- infra errors (the error-budget gate): 0 (rate 0.0); reasons: {}
- policy stratum (not qualified, not infra errors): expected blocks 0, FALSE-POSITIVE blocks 996 of 57300 benign (FP rate 0.017382198952879582), other blocks 0, policy misses 0
- policy cohort latency policy_block_fp (client total ms): n=996 p50=7.4351 p90=9.8199 p99=12.5181 p99.9=16.8979 max=16.8979 mean=7.3921
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=57300 p50=0.083 p90=0.0915 p99=0.1013 p99.9=0.1157 max=0.5282 mean=0.0826
- join: {'joined': 56304, 'by_nonce_fallback': 0, 'provider_records': 70382, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=56304 p50=7.8489 p90=10.3366 p99=12.6059 p99.9=14.9574 max=19.7573 mean=7.433 |
| T_addon_first | n=56304 p50=7.8493 p90=10.4662 p99=24.6685 p99.9=30.5491 max=48.8618 mean=7.6398 |
| T_release_lag_max | n=56304 p50=45.24 p90=50.4627 p99=68.6311 p99.9=73.2996 max=104.6058 mean=35.3546 |
| T_release_lag_max_arrival | n=56304 p50=28.6149 p90=32.719 p99=35.597 p99.9=37.8731 max=42.0364 mean=23.3831 |
| T_fw_addon | n=56304 p50=45.24 p90=50.4627 p99=68.6311 p99.9=73.2996 max=104.6058 mean=35.3546 |
| T_fw_addon_sse | n=39426 p50=47.7362 p90=51.1721 p99=69.1338 p99.9=84.4786 max=104.6058 mean=47.28 |
| T_fw_addon_json | n=16878 p50=7.9034 p90=10.4832 p99=12.7004 p99.9=14.9905 max=19.7573 mean=7.4974 |
| client_ttft_sse | n=39426 p50=157.8673 p90=160.5054 p99=177.3458 p99.9=180.9576 max=198.885 mean=157.7425 |
| provider_sched_err_last | n=56304 p50=0.0359 p90=0.0861 p99=0.1159 p99.9=0.1403 max=0.2046 mean=0.0414 |
| provider_sched_err_max_per_stream | n=56304 p50=0.1039 p90=0.1446 p99=0.1875 p99.9=0.228 max=0.7603 mean=0.0952 |
| provider_write_max | n=56304 p50=0.0203 p90=0.0272 p99=0.0378 p99.9=0.0505 max=0.755 mean=0.0198 |

- release lag: 56304 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s1b-191/rv-split-lg-1/lg', 'samples': 300, 'busy_max': 20.732707985450325, 'busy_mean': 8.92, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-b'}]
- provider CPU: {'samples': 431, 'busy_max': 24.773406664000895, 'busy_p95': 13.303632194906612}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s1b-191/rv-split-lg-1/lg', 'scheduled': 71625, 'recorded': 71625, 'interrupted': False}]
- health olg rv-split-lg-1: gc_cycles=0 sched_latency_max_ms=0.16384 tcp={'TcpRetransSegs': 7, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 2, 'TcpExtTCPTimeouts': 6, 'TcpOutSegs': 6453812, 'TcpInSegs': 11124483}
- health synthprov rv-split-prov-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 16, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 11, 'TcpExtTCPTimeouts': 6, 'TcpOutSegs': 11400919, 'TcpInSegs': 9469613}
