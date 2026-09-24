# Harness analysis (sut, policy={'blocks_total': 695, 'expected_blocks': 0, 'false_positive_blocks': 695, 'other_blocks': 0, 'benign_offered': 36762, 'false_positive_rate': 0.01890539143680975, 'policy_misses': 0, 'latency_client_total_ms': {'policy_block_fp': {'n': 695, 'min': 3.1975, 'p50': 7.277, 'p90': 12.8429, 'p99': 24.5565, 'p999': 27.6256, 'max': 27.6256, 'mean': 8.6603}}, 'block_rule': {'statuses': [400, 403, 422, 451], 'envelope_re': '(?i)content_filter|content_polic|polic(y|ies)_|blocked|block_|guard|violation|unsafe|threat'}}) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| infra_error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 36762 over 300 s = 122.54 req/s (configured 122.0)
- qualified: 36067 (120.22 req/s; dispositions ['ALLOW', 'FLAG', 'REDACT'], source auto)
- infra errors (the error-budget gate): 0 (rate 0.0); reasons: {}
- policy stratum (not qualified, not infra errors): expected blocks 0, FALSE-POSITIVE blocks 695 of 36762 benign (FP rate 0.01890539143680975), other blocks 0, policy misses 0
- policy cohort latency policy_block_fp (client total ms): n=695 p50=7.277 p90=12.8429 p99=24.5565 p99.9=27.6256 max=27.6256 mean=8.6603
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=36762 p50=0.0826 p90=0.0926 p99=0.1046 p99.9=0.1201 max=0.3545 mean=0.0811
- join: {'joined': 36067, 'by_nonce_fallback': 0, 'provider_records': 45062, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=36067 p50=7.4576 p90=12.0756 p99=18.946 p99.9=24.7432 max=32.0307 mean=7.9111 |
| T_addon_first | n=36067 p50=7.4604 p90=12.4217 p99=24.4449 p99.9=32.1986 max=54.1519 mean=8.1229 |
| T_release_lag_max | n=36067 p50=44.8899 p90=52.091 p99=69.841 p99.9=81.9648 max=92.8074 mean=35.721 |
| T_release_lag_max_arrival | n=36067 p50=27.7413 p90=33.2987 p99=40.3587 p99.9=47.1953 max=56.3387 mean=23.3358 |
| T_fw_addon | n=36067 p50=44.8899 p90=52.091 p99=69.841 p99.9=81.9648 max=92.8074 mean=35.721 |
| T_fw_addon_sse | n=25260 p50=47.3511 p90=54.1651 p99=70.97 p99.9=84.246 max=92.8074 mean=47.5954 |
| T_fw_addon_json | n=10807 p50=7.5101 p90=12.1444 p99=19.066 p99.9=24.1944 max=30.4513 mean=7.966 |
| client_ttft_sse | n=25260 p50=157.4801 p90=162.598 p99=176.9603 p99.9=183.7941 max=204.2211 mean=158.2348 |
| provider_sched_err_last | n=36067 p50=0.0409 p90=0.0876 p99=0.1132 p99.9=0.1341 max=0.1907 mean=0.0444 |
| provider_sched_err_max_per_stream | n=36067 p50=0.093 p90=0.1329 p99=0.1724 p99.9=0.1981 max=0.5879 mean=0.0879 |
| provider_write_max | n=36067 p50=0.0196 p90=0.0262 p99=0.0372 p99.9=0.0482 max=0.2908 mean=0.0192 |

- release lag: 36067 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s1p-122/rv-split-lg-1/lg', 'samples': 300, 'busy_max': 12.845890786594182, 'busy_mean': 9.45, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 17.356381031763146, 'busy_p95': 10.562432061819194}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s1p-122/rv-split-lg-1/lg', 'scheduled': 45899, 'recorded': 45899, 'interrupted': False}]
- health olg rv-split-lg-1: gc_cycles=0 sched_latency_max_ms=0.08192 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 4319375, 'TcpInSegs': 7114212}
- health synthprov rv-split-prov-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 5, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 5, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 7293523, 'TcpInSegs': 6311594}
