# Harness analysis (sut, policy={'blocks_total': 982, 'expected_blocks': 0, 'false_positive_blocks': 982, 'other_blocks': 0, 'benign_offered': 57300, 'false_positive_rate': 0.01713787085514834, 'policy_misses': 0, 'latency_client_total_ms': {'policy_block_fp': {'n': 982, 'min': 3.1941, 'p50': 7.0518, 'p90': 9.2283, 'p99': 10.5148, 'p999': 15.9475, 'max': 15.9475, 'mean': 6.9289}}, 'block_rule': {'statuses': [400, 403, 422, 451], 'envelope_re': '(?i)content_filter|content_polic|polic(y|ies)_|blocked|block_|guard|violation|unsafe|threat'}}) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| infra_error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 57300 over 300 s = 191.0 req/s (configured 191.0)
- qualified: 56318 (187.73 req/s; dispositions ['ALLOW', 'FLAG', 'REDACT'], source auto)
- infra errors (the error-budget gate): 0 (rate 0.0); reasons: {}
- policy stratum (not qualified, not infra errors): expected blocks 0, FALSE-POSITIVE blocks 982 of 57300 benign (FP rate 0.01713787085514834), other blocks 0, policy misses 0
- policy cohort latency policy_block_fp (client total ms): n=982 p50=7.0518 p90=9.2283 p99=10.5148 p99.9=15.9475 max=15.9475 mean=6.9289
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=57300 p50=0.0831 p90=0.0936 p99=0.1066 p99.9=0.1226 max=0.3348 mean=0.0825
- join: {'joined': 56318, 'by_nonce_fallback': 0, 'provider_records': 70397, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=56318 p50=7.2299 p90=9.4438 p99=11.3172 p99.9=13.6712 max=36.3765 mean=6.7739 |
| T_addon_first | n=56318 p50=7.2198 p90=9.7045 p99=24.2437 p99.9=29.8839 max=64.8169 mean=6.989 |
| T_release_lag_max | n=56318 p50=44.6646 p90=49.5709 p99=67.8765 p99.9=71.5276 max=104.1529 mean=34.6113 |
| T_release_lag_max_arrival | n=56318 p50=27.4966 p90=31.3684 p99=34.2987 p99.9=45.2639 max=59.461 mean=22.4091 |
| T_fw_addon | n=56318 p50=44.6646 p90=49.5709 p99=67.8765 p99.9=71.5276 max=104.1529 mean=34.6113 |
| T_fw_addon_sse | n=39435 p50=47.1254 p90=50.2343 p99=68.2998 p99.9=73.1398 max=104.1529 mean=46.502 |
| T_fw_addon_json | n=16883 p50=7.2817 p90=9.5634 p99=11.3643 p99.9=13.5653 max=31.8582 mean=6.8371 |
| client_ttft_sse | n=39435 p50=157.2381 p90=159.786 p99=176.5516 p99.9=180.2834 max=214.8304 mean=157.0958 |
| provider_sched_err_last | n=56318 p50=0.0351 p90=0.0859 p99=0.1153 p99.9=0.14 max=0.337 mean=0.0411 |
| provider_sched_err_max_per_stream | n=56318 p50=0.1014 p90=0.1427 p99=0.1834 p99.9=0.211 max=0.3637 mean=0.0931 |
| provider_write_max | n=56318 p50=0.0203 p90=0.0274 p99=0.0389 p99.9=0.0508 max=0.4036 mean=0.0199 |

- release lag: 56318 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s1-191-r3/rv-split-lg-1/lg', 'samples': 300, 'busy_max': 16.24991584749572, 'busy_mean': 12.92, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 23.79960708545714, 'busy_p95': 13.4778120222663}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s1-191-r3/rv-split-lg-1/lg', 'scheduled': 71625, 'recorded': 71625, 'interrupted': False}]
- health olg rv-split-lg-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 3, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 3, 'TcpOutSegs': 6446074, 'TcpInSegs': 11121877}
- health synthprov rv-split-prov-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 6, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 6, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 11402491, 'TcpInSegs': 9458711}
