# Harness analysis (sut, policy={'blocks_total': 1600, 'expected_blocks': 1463, 'false_positive_blocks': 102, 'other_blocks': 35, 'benign_offered': 12508, 'false_positive_rate': 0.008154780940198274, 'policy_misses': 591, 'latency_client_total_ms': {'policy_block_expected': {'n': 1463, 'min': 3.9826, 'p50': 5.2627, 'p90': 5.974, 'p99': 8.0855, 'p999': 13.2668, 'max': 13.3177, 'mean': 5.2834}, 'policy_block_fp': {'n': 102, 'min': 4.198, 'p50': 5.3506, 'p90': 6.1489, 'p99': 11.5386, 'p999': 11.8511, 'max': 11.8511, 'mean': 5.4827}, 'policy_block_other': {'n': 35, 'min': 4.5186, 'p50': 5.36, 'p90': 6.1657, 'p99': 7.9404, 'p999': 7.9404, 'max': 7.9404, 'mean': 5.4577}}, 'block_rule': {'statuses': [400, 403, 422, 451], 'envelope_re': '(?i)content_filter|content_polic|polic(y|ies)_|blocked|block_|guard|violation|unsafe|threat'}}) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| infra_error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | FAIL |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 21000 over 600 s = 35.0 req/s (configured 35.0)
- qualified: 18809 (31.35 req/s; dispositions ['ALLOW', 'FLAG', 'REDACT'], source auto)
- infra errors (the error-budget gate): 0 (rate 0.0); reasons: {'expected_block_not_blocked': 591}
- policy stratum (not qualified, not infra errors): expected blocks 1463, FALSE-POSITIVE blocks 102 of 12508 benign (FP rate 0.008154780940198274), other blocks 35, policy misses 591
- policy cohort latency policy_block_expected (client total ms): n=1463 p50=5.2627 p90=5.974 p99=8.0855 p99.9=13.2668 max=13.3177 mean=5.2834
- policy cohort latency policy_block_fp (client total ms): n=102 p50=5.3506 p90=6.1489 p99=11.5386 p99.9=11.8511 max=11.8511 mean=5.4827
- policy cohort latency policy_block_other (client total ms): n=35 p50=5.36 p90=6.1657 p99=7.9404 p99.9=7.9404 max=7.9404 mean=5.4577
- safety failures: 1182 {'block_expected_but_provider_called': 591, 'canary_reached_provider': 591}
- schedule drops (>5.0 ms): 0; lateness ms: n=21000 p50=0.0834 p90=0.0956 p99=0.1107 p99.9=0.1357 max=0.3421 mean=0.0841
- join: {'joined': 19400, 'by_nonce_fallback': 0, 'provider_records': 21840, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=18809 p50=6.1075 p90=6.954 p99=11.4427 p99.9=13.9049 max=18.575 mean=6.2083 |
| T_addon_first | n=18809 p50=6.0426 p90=6.9306 p99=25.0936 p99.9=26.7669 max=45.739 mean=6.3655 |
| T_release_lag_max | n=1406 p50=45.0716 p90=46.6084 p99=65.8143 p99.9=71.4497 max=72.998 mean=32.0978 |
| T_release_lag_max_arrival | n=1406 p50=27.4665 p90=32.6345 p99=34.3443 p99.9=38.3211 max=38.7918 mean=22.5215 |
| T_fw_addon | n=18809 p50=6.1775 p90=7.6163 p99=46.4611 p99.9=65.6207 max=72.998 mean=8.3807 |
| T_fw_addon_sse | n=13202 p50=6.1803 p90=11.6522 p99=46.6495 p99.9=65.9191 max=72.998 mean=9.2791 |
| T_fw_addon_json | n=5607 p50=6.1713 p90=7.008 p99=11.5832 p99.9=13.8956 max=14.4272 mean=6.2653 |
| client_ttft_sse | n=13202 p50=156.0353 p90=156.9387 p99=175.6093 p99.9=177.0061 max=195.764 mean=156.4605 |
| provider_sched_err_last | n=18809 p50=0.0527 p90=0.0879 p99=0.105 p99.9=0.1183 max=0.1346 mean=0.0524 |
| provider_sched_err_max_per_stream | n=18809 p50=0.074 p90=0.1105 p99=0.1279 p99.9=0.162 max=0.2081 mean=0.0718 |
| provider_write_max | n=18809 p50=0.0104 p90=0.0202 p99=0.029 p99.9=0.0362 max=0.0756 mean=0.0125 |

- release lag: 1406 sampled streams joined, 458 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/c22-035/rv-pbu-lg-1/lg', 'samples': 600, 'busy_max': 17.013497210684967, 'busy_mean': 3.8, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/c22-035/rv-pbu-lg-2/lg', 'samples': 600, 'busy_max': 16.201290101316744, 'busy_mean': 3.62, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 726, 'busy_max': 17.569746161567735, 'busy_p95': 4.998024244853461}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/c22-035/rv-pbu-lg-1/lg', 'scheduled': 11813, 'recorded': 11813, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/c22-035/rv-pbu-lg-2/lg', 'scheduled': 11812, 'recorded': 11812, 'interrupted': False}]
- health olg rv-pbu-lg-1: gc_cycles=0 sched_latency_max_ms=0.16384 tcp={'TcpRetransSegs': 10, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 10, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 687636, 'TcpInSegs': 940499}
- health olg rv-pbu-lg-2: gc_cycles=0 sched_latency_max_ms=0.049152 tcp={'TcpRetransSegs': 12, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 12, 'TcpExtTCPTimeouts': 1, 'TcpOutSegs': 690279, 'TcpInSegs': 939577}
- health synthprov rv-pbu-prov-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 13, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 13, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1935069, 'TcpInSegs': 1536895}
