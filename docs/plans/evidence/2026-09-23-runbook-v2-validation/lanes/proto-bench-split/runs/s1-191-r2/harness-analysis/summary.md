# Harness analysis (sut, policy={'blocks_total': 959, 'expected_blocks': 0, 'false_positive_blocks': 959, 'other_blocks': 0, 'benign_offered': 57300, 'false_positive_rate': 0.016736474694589876, 'policy_misses': 0, 'latency_client_total_ms': {'policy_block_fp': {'n': 959, 'min': 3.2482, 'p50': 7.0675, 'p90': 9.2986, 'p99': 10.6919, 'p999': 15.7002, 'max': 15.7002, 'mean': 6.9839}}, 'block_rule': {'statuses': [400, 403, 422, 451], 'envelope_re': '(?i)content_filter|content_polic|polic(y|ies)_|blocked|block_|guard|violation|unsafe|threat'}}) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| infra_error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 57300 over 300 s = 191.0 req/s (configured 191.0)
- qualified: 56341 (187.8 req/s; dispositions ['ALLOW', 'FLAG', 'REDACT'], source auto)
- infra errors (the error-budget gate): 0 (rate 0.0); reasons: {}
- policy stratum (not qualified, not infra errors): expected blocks 0, FALSE-POSITIVE blocks 959 of 57300 benign (FP rate 0.016736474694589876), other blocks 0, policy misses 0
- policy cohort latency policy_block_fp (client total ms): n=959 p50=7.0675 p90=9.2986 p99=10.6919 p99.9=15.7002 max=15.7002 mean=6.9839
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=57300 p50=0.0843 p90=0.0948 p99=0.1089 p99.9=0.1252 max=0.2322 mean=0.0837
- join: {'joined': 56341, 'by_nonce_fallback': 0, 'provider_records': 70428, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=56341 p50=7.2568 p90=9.495 p99=11.4033 p99.9=13.907 max=30.8397 mean=6.8097 |
| T_addon_first | n=56341 p50=7.2464 p90=9.737 p99=24.4257 p99.9=30.1397 max=48.0629 mean=7.0314 |
| T_release_lag_max | n=56341 p50=44.7046 p90=49.5676 p99=67.8774 p99.9=70.8479 max=91.2516 mean=34.6503 |
| T_release_lag_max_arrival | n=56341 p50=27.5454 p90=31.418 p99=34.2068 p99.9=40.9663 max=52.3503 mean=22.4391 |
| T_fw_addon | n=56341 p50=44.7046 p90=49.5676 p99=67.8774 p99.9=70.8479 max=91.2516 mean=34.6503 |
| T_fw_addon_sse | n=39452 p50=47.1498 p90=50.2521 p99=68.3559 p99.9=71.8699 max=91.2516 mean=46.5395 |
| T_fw_addon_json | n=16889 p50=7.318 p90=9.646 p99=11.4797 p99.9=13.9483 max=28.4873 mean=6.8778 |
| client_ttft_sse | n=39452 p50=157.2592 p90=159.8054 p99=176.804 p99.9=180.4035 max=198.0716 mean=157.1388 |
| provider_sched_err_last | n=56341 p50=0.0357 p90=0.086 p99=0.1141 p99.9=0.1395 max=0.2232 mean=0.0413 |
| provider_sched_err_max_per_stream | n=56341 p50=0.1025 p90=0.1435 p99=0.1844 p99.9=0.2117 max=0.2759 mean=0.094 |
| provider_write_max | n=56341 p50=0.0203 p90=0.0273 p99=0.0386 p99.9=0.0506 max=0.1407 mean=0.0198 |

- release lag: 56341 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s1-191-r2/rv-split-lg-1/lg', 'samples': 300, 'busy_max': 16.289463662314084, 'busy_mean': 13.12, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 13.810652429188607, 'busy_p95': 13.55985056786535}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s1-191-r2/rv-split-lg-1/lg', 'scheduled': 71625, 'recorded': 71625, 'interrupted': False}]
- health olg rv-split-lg-1: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 3, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 3, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 6444470, 'TcpInSegs': 11125909}
- health synthprov rv-split-prov-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 4, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 4, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 11407160, 'TcpInSegs': 9464446}
