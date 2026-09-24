# Harness analysis (sut, policy={'blocks_total': 644, 'expected_blocks': 0, 'false_positive_blocks': 644, 'other_blocks': 0, 'benign_offered': 36600, 'false_positive_rate': 0.017595628415300546, 'policy_misses': 0, 'latency_client_total_ms': {'policy_block_fp': {'n': 644, 'min': 3.2033, 'p50': 6.8991, 'p90': 9.2303, 'p99': 10.1024, 'p999': 12.037, 'max': 12.037, 'mean': 6.8074}}, 'block_rule': {'statuses': [400, 403, 422, 451], 'envelope_re': '(?i)content_filter|content_polic|polic(y|ies)_|blocked|block_|guard|violation|unsafe|threat'}}) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| infra_error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 36600 over 300 s = 122.0 req/s (configured 122.0)
- qualified: 35956 (119.85 req/s; dispositions ['ALLOW', 'FLAG', 'REDACT'], source auto)
- infra errors (the error-budget gate): 0 (rate 0.0); reasons: {}
- policy stratum (not qualified, not infra errors): expected blocks 0, FALSE-POSITIVE blocks 644 of 36600 benign (FP rate 0.017595628415300546), other blocks 0, policy misses 0
- policy cohort latency policy_block_fp (client total ms): n=644 p50=6.8991 p90=9.2303 p99=10.1024 p99.9=12.037 max=12.037 mean=6.8074
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=36600 p50=0.0846 p90=0.0949 p99=0.1076 p99.9=0.1289 max=0.3147 mean=0.0845
- join: {'joined': 35956, 'by_nonce_fallback': 0, 'provider_records': 44977, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=35956 p50=7.0096 p90=8.5508 p99=10.9694 p99.9=12.7732 max=29.807 mean=6.4349 |
| T_addon_first | n=35956 p50=7.0185 p90=8.7459 p99=23.9447 p99.9=29.1964 max=48.2025 mean=6.6285 |
| T_release_lag_max | n=35956 p50=44.4161 p90=48.6719 p99=67.5475 p99.9=71.0623 max=89.7036 mean=34.2374 |
| T_release_lag_max_arrival | n=35956 p50=26.835 p90=30.6109 p99=33.5842 p99.9=43.0638 max=52.2798 mean=21.841 |
| T_fw_addon | n=35956 p50=44.4161 p90=48.6719 p99=67.5475 p99.9=71.0623 max=89.7036 mean=34.2374 |
| T_fw_addon_sse | n=25183 p50=46.8562 p90=49.9468 p99=67.8032 p99.9=73.6387 max=89.7036 mean=46.0932 |
| T_fw_addon_json | n=10773 p50=7.0841 p90=8.6672 p99=11.1173 p99.9=13.1338 max=27.7236 mean=6.5231 |
| client_ttft_sse | n=25183 p50=157.0339 p90=158.8311 p99=174.586 p99.9=179.8574 max=198.2082 mean=156.718 |
| provider_sched_err_last | n=35956 p50=0.0412 p90=0.0874 p99=0.1131 p99.9=0.1336 max=0.1941 mean=0.0445 |
| provider_sched_err_max_per_stream | n=35956 p50=0.0921 p90=0.133 p99=0.1731 p99.9=0.201 max=0.2345 mean=0.0874 |
| provider_write_max | n=35956 p50=0.0199 p90=0.0264 p99=0.0374 p99.9=0.0488 max=0.0902 mean=0.0193 |

- release lag: 35956 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s1-122/rv-split-lg-1/lg', 'samples': 300, 'busy_max': 12.757332554948487, 'busy_mean': 8.86, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 17.22257073293213, 'busy_p95': 10.444575766350406}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s1-122/rv-split-lg-1/lg', 'scheduled': 45750, 'recorded': 45750, 'interrupted': False}]
- health olg rv-split-lg-1: gc_cycles=0 sched_latency_max_ms=0.13107200000000002 tcp={'TcpRetransSegs': 1, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 1, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 4131094, 'TcpInSegs': 7098956}
- health synthprov rv-split-prov-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 3, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 3, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 7277806, 'TcpInSegs': 6175439}
