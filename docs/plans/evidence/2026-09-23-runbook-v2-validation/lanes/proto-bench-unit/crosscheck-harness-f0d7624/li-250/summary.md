# Harness analysis (sut, policy={'blocks_total': 1333, 'expected_blocks': 0, 'false_positive_blocks': 1333, 'other_blocks': 0, 'benign_offered': 75000, 'false_positive_rate': 0.017773333333333332, 'policy_misses': 0, 'latency_client_total_ms': {'policy_block_fp': {'n': 1333, 'min': 4.6689, 'p50': 11.9479, 'p90': 15.0531, 'p99': 19.2981, 'p999': 22.9017, 'max': 24.4538, 'mean': 11.8123}}, 'block_rule': {'statuses': [400, 403, 422, 451], 'envelope_re': '(?i)content_filter|content_polic|polic(y|ies)_|blocked|block_|guard|violation|unsafe|threat'}}) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| infra_error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 75000 over 300 s = 250.0 req/s (configured 250.0)
- qualified: 73667 (245.56 req/s; dispositions ['ALLOW', 'FLAG', 'REDACT'], source auto)
- infra errors (the error-budget gate): 0 (rate 0.0); reasons: {}
- policy stratum (not qualified, not infra errors): expected blocks 0, FALSE-POSITIVE blocks 1333 of 75000 benign (FP rate 0.017773333333333332), other blocks 0, policy misses 0
- policy cohort latency policy_block_fp (client total ms): n=1333 p50=11.9479 p90=15.0531 p99=19.2981 p99.9=22.9017 max=24.4538 mean=11.8123
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=75000 p50=0.0837 p90=0.0931 p99=0.104 p99.9=0.1249 max=0.343 mean=0.0839
- join: {'joined': 73667, 'by_nonce_fallback': 0, 'provider_records': 92127, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=73667 p50=11.884 p90=15.8767 p99=20.4162 p99.9=24.8596 max=64.9477 mean=11.5637 |
| T_addon_first | n=73667 p50=11.828 p90=15.9899 p99=27.4294 p99.9=36.5312 max=64.8421 mean=11.7009 |
| T_release_lag_max | n=73667 p50=48.1705 p90=55.9207 p99=73.8044 p99.9=83.2667 max=106.6325 mean=39.3374 |
| T_release_lag_max_arrival | n=73667 p50=29.6124 p90=36.1591 p99=43.4383 p99.9=72.9608 max=85.2841 mean=26.3182 |
| T_fw_addon | n=73667 p50=48.1705 p90=55.9207 p99=73.8044 p99.9=83.2667 max=106.6325 mean=39.3374 |
| T_fw_addon_sse | n=51590 p50=51.644 p90=57.6975 p99=74.7915 p99.9=88.4813 max=106.6325 mean=51.2181 |
| T_fw_addon_json | n=22077 p50=11.8828 p90=15.9156 p99=20.2996 p99.9=24.7721 max=61.0263 mean=11.5743 |
| client_ttft_sse | n=51590 p50=161.8422 p90=166.0663 p99=181.0872 p99.9=188.418 max=214.8739 mean=161.7948 |
| provider_sched_err_last | n=73667 p50=0.0325 p90=0.0856 p99=0.1181 p99.9=0.1447 max=0.209 mean=0.0396 |
| provider_sched_err_max_per_stream | n=73667 p50=0.1085 p90=0.1489 p99=0.1864 p99.9=0.2118 max=0.2826 mean=0.0976 |
| provider_write_max | n=73667 p50=0.0195 p90=0.0262 p99=0.0361 p99.9=0.0462 max=0.0891 mean=0.019 |

- release lag: 73667 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/li-250/rv-pbu-lg-3/lg', 'samples': 300, 'busy_max': 12.620697844425555, 'busy_mean': 9.32, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/li-250/rv-pbu-lg-4/lg', 'samples': 300, 'busy_max': 9.649302265618786, 'busy_mean': 9.1, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 27.373398255022273, 'busy_p95': 14.994515780212081}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/li-250/rv-pbu-lg-3/lg', 'scheduled': 46875, 'recorded': 46875, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/li-250/rv-pbu-lg-4/lg', 'scheduled': 46875, 'recorded': 46875, 'interrupted': False}]
- health olg rv-pbu-lg-3: gc_cycles=0 sched_latency_max_ms=0.13107200000000002 tcp={'TcpRetransSegs': 1, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 1, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 4324431, 'TcpInSegs': 7240311}
- health olg rv-pbu-lg-4: gc_cycles=0 sched_latency_max_ms=0.13107200000000002 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 4302443, 'TcpInSegs': 7239007}
- health synthprov rv-pbu-prov-2: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 76, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 65, 'TcpExtTCPTimeouts': 12, 'TcpOutSegs': 14865347, 'TcpInSegs': 9959605}
