# Harness analysis (sut, policy={'blocks_total': 1602, 'expected_blocks': 0, 'false_positive_blocks': 1602, 'other_blocks': 0, 'benign_offered': 89400, 'false_positive_rate': 0.017919463087248323, 'policy_misses': 0, 'latency_client_total_ms': {'policy_block_fp': {'n': 1602, 'min': 3.3766, 'p50': 7.7231, 'p90': 10.2139, 'p99': 14.6119, 'p999': 18.5902, 'max': 21.1452, 'mean': 7.9172}}, 'block_rule': {'statuses': [400, 403, 422, 451], 'envelope_re': '(?i)content_filter|content_polic|polic(y|ies)_|blocked|block_|guard|violation|unsafe|threat'}}) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| infra_error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 89400 over 300 s = 298.0 req/s (configured 298.0)
- qualified: 87798 (292.66 req/s; dispositions ['ALLOW', 'FLAG', 'REDACT'], source auto)
- infra errors (the error-budget gate): 0 (rate 0.0); reasons: {}
- policy stratum (not qualified, not infra errors): expected blocks 0, FALSE-POSITIVE blocks 1602 of 89400 benign (FP rate 0.017919463087248323), other blocks 0, policy misses 0
- policy cohort latency policy_block_fp (client total ms): n=1602 p50=7.7231 p90=10.2139 p99=14.6119 p99.9=18.5902 max=21.1452 mean=7.9172
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=89400 p50=0.0856 p90=0.0964 p99=0.1088 p99.9=0.1266 max=0.5855 mean=0.0854
- join: {'joined': 87798, 'by_nonce_fallback': 0, 'provider_records': 109784, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=87798 p50=8.0284 p90=11.0105 p99=15.3906 p99.9=19.6307 max=25.4428 mean=7.8646 |
| T_addon_first | n=87798 p50=8.0178 p90=11.105 p99=24.5112 p99.9=31.2035 max=55.0284 mean=8.0687 |
| T_release_lag_max | n=87798 p50=45.2298 p90=51.1003 p99=69.3234 p99.9=76.5002 max=109.4233 mean=35.7852 |
| T_release_lag_max_arrival | n=87798 p50=28.7308 p90=33.4296 p99=37.9668 p99.9=42.072 max=53.8416 mean=23.7635 |
| T_fw_addon | n=87798 p50=45.2298 p90=51.1003 p99=69.3234 p99.9=76.5002 max=109.4233 mean=35.7852 |
| T_fw_addon_sse | n=61492 p50=47.9119 p90=52.7523 p99=70.2023 p99.9=84.4427 max=109.4233 mean=47.6991 |
| T_fw_addon_json | n=26306 p50=8.081 p90=11.0755 p99=15.4635 p99.9=19.9941 max=25.4428 mean=7.9356 |
| client_ttft_sse | n=61492 p50=158.0342 p90=161.1685 p99=177.25 p99.9=182.4695 max=205.0364 mean=158.1687 |
| provider_sched_err_last | n=87798 p50=0.0385 p90=0.0857 p99=0.1137 p99.9=0.1341 max=0.2237 mean=0.0427 |
| provider_sched_err_max_per_stream | n=87798 p50=0.0967 p90=0.138 p99=0.1804 p99.9=0.2135 max=0.7049 mean=0.0907 |
| provider_write_max | n=87798 p50=0.0203 p90=0.028 p99=0.0379 p99.9=0.0481 max=0.1604 mean=0.0198 |

- release lag: 87798 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s3b-298-r3/rv-split-lg-1/lg', 'samples': 300, 'busy_max': 20.92911789830846, 'busy_mean': 7.72, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-b'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s3b-298-r3/rv-split-lg-2/lg', 'samples': 300, 'busy_max': 22.716973991155744, 'busy_mean': 11.12, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-b'}]
- provider CPU: {'samples': 866, 'busy_max': 23.991775685278693, 'busy_p95': 11.564471174789526}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s3b-298-r3/rv-split-lg-1/lg', 'scheduled': 55875, 'recorded': 55875, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s3b-298-r3/rv-split-lg-2/lg', 'scheduled': 55875, 'recorded': 55875, 'interrupted': False}]
- health olg rv-split-lg-1: gc_cycles=0 sched_latency_max_ms=0.16384 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 5069084, 'TcpInSegs': 8716637}
- health olg rv-split-lg-2: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 56, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 4, 'TcpExtTCPTimeouts': 60, 'TcpOutSegs': 5076165, 'TcpInSegs': 8709286}
- health synthprov rv-split-prov-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 14, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 14, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 8893895, 'TcpInSegs': 7537523}
- health synthprov rv-split-prov-2: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 14, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 14, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 8888984, 'TcpInSegs': 7515974}
