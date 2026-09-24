# Harness analysis (sut, policy={'blocks_total': 2154, 'expected_blocks': 0, 'false_positive_blocks': 2154, 'other_blocks': 0, 'benign_offered': 111675, 'false_positive_rate': 0.01928811282740094, 'policy_misses': 0, 'latency_client_total_ms': {'policy_block_fp': {'n': 2154, 'min': 3.1692, 'p50': 9.7501, 'p90': 22.973, 'p99': 26.364, 'p999': 29.4445, 'max': 30.9499, 'mean': 11.7107}}, 'block_rule': {'statuses': [400, 403, 422, 451], 'envelope_re': '(?i)content_filter|content_polic|polic(y|ies)_|blocked|block_|guard|violation|unsafe|threat'}}) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| infra_error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 111675 over 300 s = 372.25 req/s (configured 373.0)
- qualified: 109521 (365.07 req/s; dispositions ['ALLOW', 'FLAG', 'REDACT'], source auto)
- infra errors (the error-budget gate): 0 (rate 0.0); reasons: {}
- policy stratum (not qualified, not infra errors): expected blocks 0, FALSE-POSITIVE blocks 2154 of 111675 benign (FP rate 0.01928811282740094), other blocks 0, policy misses 0
- policy cohort latency policy_block_fp (client total ms): n=2154 p50=9.7501 p90=22.973 p99=26.364 p99.9=29.4445 max=30.9499 mean=11.7107
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=111675 p50=0.0833 p90=0.0966 p99=0.115 p99.9=0.1376 max=0.2521 mean=0.0814
- join: {'joined': 109521, 'by_nonce_fallback': 0, 'provider_records': 137217, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=109521 p50=9.0295 p90=15.9551 p99=23.6032 p99.9=28.476 max=54.5083 mean=9.995 |
| T_addon_first | n=109521 p50=9.0695 p90=16.3763 p99=27.4814 p99.9=37.9305 max=67.6341 mean=10.2115 |
| T_release_lag_max | n=109521 p50=47.1389 p90=56.29 p99=72.5686 p99.9=83.9112 max=107.8606 mean=37.9757 |
| T_release_lag_max_arrival | n=109521 p50=30.2065 p90=37.9946 p99=47.8973 p99.9=60.0405 max=77.6217 mean=26.2738 |
| T_fw_addon | n=109521 p50=47.1389 p90=56.29 p99=72.5686 p99.9=83.9112 max=107.8606 mean=37.9757 |
| T_fw_addon_sse | n=76704 p50=49.1032 p90=59.0133 p99=73.9784 p99.9=86.036 max=107.8606 mean=49.9183 |
| T_fw_addon_json | n=32817 p50=9.1081 p90=16.0484 p99=23.4394 p99.9=28.5461 max=47.109 mean=10.0619 |
| client_ttft_sse | n=76704 p50=159.0912 p90=166.5687 p99=178.755 p99.9=189.3643 max=217.7039 mean=160.3131 |
| provider_sched_err_last | n=109521 p50=0.0281 p90=0.0846 p99=0.121 p99.9=0.156 max=0.2208 mean=0.0375 |
| provider_sched_err_max_per_stream | n=109521 p50=0.1186 p90=0.161 p99=0.2001 p99.9=0.2297 max=0.6214 mean=0.1044 |
| provider_write_max | n=109521 p50=0.0214 p90=0.0298 p99=0.0418 p99.9=0.0533 max=0.3332 mean=0.0211 |

- release lag: 109521 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s2p-373/rv-split-lg-1/lg', 'samples': 300, 'busy_max': 32.73155398674855, 'busy_mean': 22.41, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 431, 'busy_max': 31.32734112682164, 'busy_p95': 20.130542129030506}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s2p-373/rv-split-lg-1/lg', 'scheduled': 139929, 'recorded': 139929, 'interrupted': False}]
- health olg rv-split-lg-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 8, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 8, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 12790810, 'TcpInSegs': 21692215}
- health synthprov rv-split-prov-1: gc_cycles=0 sched_latency_max_ms=0.13107200000000002 tcp={'TcpRetransSegs': 75, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 75, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 22234328, 'TcpInSegs': 17696156}
