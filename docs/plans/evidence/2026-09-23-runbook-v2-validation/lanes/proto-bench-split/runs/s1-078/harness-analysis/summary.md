# Harness analysis (sut, policy={'blocks_total': 415, 'expected_blocks': 0, 'false_positive_blocks': 415, 'other_blocks': 0, 'benign_offered': 23400, 'false_positive_rate': 0.017735042735042734, 'policy_misses': 0, 'latency_client_total_ms': {'policy_block_fp': {'n': 415, 'min': 3.1453, 'p50': 6.7904, 'p90': 9.1607, 'p99': 9.6198, 'p999': 11.7874, 'max': 11.7874, 'mean': 6.6373}}, 'block_rule': {'statuses': [400, 403, 422, 451], 'envelope_re': '(?i)content_filter|content_polic|polic(y|ies)_|blocked|block_|guard|violation|unsafe|threat'}}) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| infra_error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 23400 over 300 s = 78.0 req/s (configured 78.0)
- qualified: 22985 (76.62 req/s; dispositions ['ALLOW', 'FLAG', 'REDACT'], source auto)
- infra errors (the error-budget gate): 0 (rate 0.0); reasons: {}
- policy stratum (not qualified, not infra errors): expected blocks 0, FALSE-POSITIVE blocks 415 of 23400 benign (FP rate 0.017735042735042734), other blocks 0, policy misses 0
- policy cohort latency policy_block_fp (client total ms): n=415 p50=6.7904 p90=9.1607 p99=9.6198 p99.9=11.7874 max=11.7874 mean=6.6373
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=23400 p50=0.0829 p90=0.0924 p99=0.1042 p99.9=0.1182 max=0.373 mean=0.0828
- join: {'joined': 22985, 'by_nonce_fallback': 0, 'provider_records': 28755, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=22985 p50=6.8977 p90=7.9603 p99=10.2067 p99.9=11.319 max=13.9663 mean=6.222 |
| T_addon_first | n=22985 p50=6.8968 p90=8.106 p99=24.0957 p99.9=29.4523 max=49.7378 mean=6.4762 |
| T_release_lag_max | n=22985 p50=44.2743 p90=48.0977 p99=67.3783 p99.9=70.125 max=90.0456 mean=34.0239 |
| T_release_lag_max_arrival | n=22985 p50=26.367 p90=29.9849 p99=32.396 p99.9=33.4206 max=35.4024 mean=21.3178 |
| T_fw_addon | n=22985 p50=44.2743 p90=48.0977 p99=67.3783 p99.9=70.125 max=90.0456 mean=34.0239 |
| T_fw_addon_sse | n=16092 p50=46.7711 p90=49.7514 p99=67.5409 p99.9=72.2523 max=90.0456 mean=45.9006 |
| T_fw_addon_json | n=6893 p50=6.9508 p90=8.0596 p99=10.2827 p99.9=11.4246 max=13.6657 mean=6.2971 |
| client_ttft_sse | n=16092 p50=156.9216 p90=158.1849 p99=174.6103 p99.9=179.7407 max=199.8532 mean=156.6002 |
| provider_sched_err_last | n=22985 p50=0.0449 p90=0.0882 p99=0.1101 p99.9=0.1279 max=0.1597 mean=0.0469 |
| provider_sched_err_max_per_stream | n=22985 p50=0.0851 p90=0.125 p99=0.1581 p99.9=0.1923 max=0.5699 mean=0.082 |
| provider_write_max | n=22985 p50=0.0195 p90=0.0258 p99=0.0366 p99.9=0.0494 max=0.1584 mean=0.0189 |

- release lag: 22985 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s1-078/rv-split-lg-1/lg', 'samples': 300, 'busy_max': 17.111457632860873, 'busy_mean': 6.12, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 21.55940888921166, 'busy_p95': 8.152299984747414}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s1-078/rv-split-lg-1/lg', 'scheduled': 29250, 'recorded': 29250, 'interrupted': False}]
- health olg rv-split-lg-1: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 1, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 1, 'TcpOutSegs': 2689597, 'TcpInSegs': 4525207}
- health synthprov rv-split-prov-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 4639823, 'TcpInSegs': 4031111}
