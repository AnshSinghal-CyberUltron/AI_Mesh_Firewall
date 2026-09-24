# Harness analysis (sut, policy={'blocks_total': 2361, 'expected_blocks': 0, 'false_positive_blocks': 2361, 'other_blocks': 0, 'benign_offered': 139800, 'false_positive_rate': 0.01688841201716738, 'policy_misses': 0, 'latency_client_total_ms': {'policy_block_fp': {'n': 2361, 'min': 3.3454, 'p50': 7.5241, 'p90': 10.0413, 'p99': 14.2019, 'p999': 17.6952, 'max': 18.9928, 'mean': 7.6967}}, 'block_rule': {'statuses': [400, 403, 422, 451], 'envelope_re': '(?i)content_filter|content_polic|polic(y|ies)_|blocked|block_|guard|violation|unsafe|threat'}}) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| infra_error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 139800 over 300 s = 466.0 req/s (configured 466.0)
- qualified: 137439 (458.13 req/s; dispositions ['ALLOW', 'FLAG', 'REDACT'], source auto)
- infra errors (the error-budget gate): 0 (rate 0.0); reasons: {}
- policy stratum (not qualified, not infra errors): expected blocks 0, FALSE-POSITIVE blocks 2361 of 139800 benign (FP rate 0.01688841201716738), other blocks 0, policy misses 0
- policy cohort latency policy_block_fp (client total ms): n=2361 p50=7.5241 p90=10.0413 p99=14.2019 p99.9=17.6952 max=18.9928 mean=7.6967
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=139800 p50=0.0851 p90=0.0969 p99=0.1111 p99.9=0.1322 max=0.742 mean=0.0849
- join: {'joined': 137439, 'by_nonce_fallback': 0, 'provider_records': 171809, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=137439 p50=7.9504 p90=10.856 p99=15.1635 p99.9=18.8938 max=34.3491 mean=7.7132 |
| T_addon_first | n=137439 p50=7.9405 p90=10.9177 p99=24.9804 p99.9=31.0557 max=70.7668 mean=7.9364 |
| T_release_lag_max | n=137439 p50=45.2277 p90=50.8742 p99=69.0151 p99.9=75.0762 max=110.6731 mean=35.6068 |
| T_release_lag_max_arrival | n=137439 p50=28.68 p90=33.3443 p99=37.9729 p99.9=44.4786 max=58.9808 mean=23.687 |
| T_fw_addon | n=137439 p50=45.2277 p90=50.8742 p99=69.0151 p99.9=75.0762 max=110.6731 mean=35.6068 |
| T_fw_addon_sse | n=96237 p50=47.822 p90=52.1699 p99=70.1345 p99.9=78.3964 max=110.6731 mean=47.5165 |
| T_fw_addon_json | n=41202 p50=8.0232 p90=10.9304 p99=15.1908 p99.9=18.851 max=31.6192 mean=7.7891 |
| client_ttft_sse | n=96237 p50=157.9478 p90=160.9571 p99=177.4869 p99.9=182.2312 max=220.8149 mean=158.0397 |
| provider_sched_err_last | n=137439 p50=0.0331 p90=0.0855 p99=0.1176 p99.9=0.1481 max=0.2351 mean=0.04 |
| provider_sched_err_max_per_stream | n=137439 p50=0.1094 p90=0.1533 p99=0.1954 p99.9=0.2244 max=0.5315 mean=0.0992 |
| provider_write_max | n=137439 p50=0.0209 p90=0.0288 p99=0.0396 p99.9=0.0499 max=0.4196 mean=0.0205 |

- release lag: 137439 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s5b-466/rv-split-lg-1/lg', 'samples': 300, 'busy_max': 13.945217307975167, 'busy_mean': 10.19, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-b'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s5b-466/rv-split-lg-2/lg', 'samples': 300, 'busy_max': 16.575188355715397, 'busy_mean': 15.68, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-b'}]
- provider CPU: {'samples': 850, 'busy_max': 18.41996114134581, 'busy_p95': 18.129547413642754}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s5b-466/rv-split-lg-1/lg', 'scheduled': 87375, 'recorded': 87375, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s5b-466/rv-split-lg-2/lg', 'scheduled': 87375, 'recorded': 87375, 'interrupted': False}]
- health olg rv-split-lg-1: gc_cycles=0 sched_latency_max_ms=0.13107200000000002 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 7820087, 'TcpInSegs': 13637443}
- health olg rv-split-lg-2: gc_cycles=0 sched_latency_max_ms=0.16384 tcp={'TcpRetransSegs': 27, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 3, 'TcpExtTCPTimeouts': 27, 'TcpOutSegs': 7838081, 'TcpInSegs': 13637954}
- health synthprov rv-split-prov-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 35, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 35, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 18564548, 'TcpInSegs': 16348568}
- health synthprov rv-split-prov-2: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 21, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 21, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 9267517, 'TcpInSegs': 8171157}
