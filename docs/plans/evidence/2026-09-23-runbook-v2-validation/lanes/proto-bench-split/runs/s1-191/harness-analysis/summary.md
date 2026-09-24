# Harness analysis (sut, policy={'blocks_total': 987, 'expected_blocks': 0, 'false_positive_blocks': 987, 'other_blocks': 0, 'benign_offered': 57300, 'false_positive_rate': 0.017225130890052356, 'policy_misses': 0, 'latency_client_total_ms': {'policy_block_fp': {'n': 987, 'min': 3.1545, 'p50': 7.0479, 'p90': 9.2816, 'p99': 10.7819, 'p999': 14.7733, 'max': 14.7733, 'mean': 6.9376}}, 'block_rule': {'statuses': [400, 403, 422, 451], 'envelope_re': '(?i)content_filter|content_polic|polic(y|ies)_|blocked|block_|guard|violation|unsafe|threat'}}) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| infra_error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 57300 over 300 s = 191.0 req/s (configured 191.0)
- qualified: 56313 (187.71 req/s; dispositions ['ALLOW', 'FLAG', 'REDACT'], source auto)
- infra errors (the error-budget gate): 0 (rate 0.0); reasons: {}
- policy stratum (not qualified, not infra errors): expected blocks 0, FALSE-POSITIVE blocks 987 of 57300 benign (FP rate 0.017225130890052356), other blocks 0, policy misses 0
- policy cohort latency policy_block_fp (client total ms): n=987 p50=7.0479 p90=9.2816 p99=10.7819 p99.9=14.7733 max=14.7733 mean=6.9376
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=57300 p50=0.0837 p90=0.0942 p99=0.1071 p99.9=0.1244 max=0.5115 mean=0.0832
- join: {'joined': 56313, 'by_nonce_fallback': 0, 'provider_records': 70393, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=56313 p50=7.232 p90=9.448 p99=11.2592 p99.9=13.6359 max=29.2844 mean=6.7833 |
| T_addon_first | n=56313 p50=7.2235 p90=9.6912 p99=24.3827 p99.9=29.916 max=52.297 mean=7.0084 |
| T_release_lag_max | n=56313 p50=44.6852 p90=49.5882 p99=67.9136 p99.9=71.6961 max=90.6775 mean=34.6529 |
| T_release_lag_max_arrival | n=56313 p50=27.3655 p90=31.1874 p99=33.8138 p99.9=39.4304 max=50.7291 mean=22.3111 |
| T_fw_addon | n=56313 p50=44.6852 p90=49.5882 p99=67.9136 p99.9=71.6961 max=90.6775 mean=34.6529 |
| T_fw_addon_sse | n=39431 p50=47.1319 p90=50.2655 p99=68.4002 p99.9=84.2803 max=90.6775 mean=46.555 |
| T_fw_addon_json | n=16882 p50=7.3056 p90=9.5696 p99=11.3198 p99.9=13.3831 max=29.2844 mean=6.8535 |
| client_ttft_sse | n=39431 p50=157.2356 p90=159.764 p99=176.1859 p99.9=180.2646 max=202.3057 mean=157.1165 |
| provider_sched_err_last | n=56313 p50=0.0356 p90=0.0858 p99=0.1146 p99.9=0.1396 max=0.2654 mean=0.0413 |
| provider_sched_err_max_per_stream | n=56313 p50=0.1007 p90=0.1426 p99=0.1838 p99.9=0.2203 max=0.6282 mean=0.0932 |
| provider_write_max | n=56313 p50=0.0204 p90=0.0283 p99=0.0394 p99.9=0.0514 max=0.1355 mean=0.0201 |

- release lag: 56313 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s1-191/rv-split-lg-1/lg', 'samples': 300, 'busy_max': 25.344193460642973, 'busy_mean': 13.02, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 23.977219487415844, 'busy_p95': 13.449860192737384}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s1-191/rv-split-lg-1/lg', 'scheduled': 71625, 'recorded': 71625, 'interrupted': False}]
- health olg rv-split-lg-1: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 6416387, 'TcpInSegs': 11120979}
- health synthprov rv-split-prov-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 4, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 4, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 11401482, 'TcpInSegs': 9425527}
