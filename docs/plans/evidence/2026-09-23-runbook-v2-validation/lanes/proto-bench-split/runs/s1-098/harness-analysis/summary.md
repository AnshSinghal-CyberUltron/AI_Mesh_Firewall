# Harness analysis (sut, policy={'blocks_total': 568, 'expected_blocks': 0, 'false_positive_blocks': 568, 'other_blocks': 0, 'benign_offered': 29400, 'false_positive_rate': 0.019319727891156463, 'policy_misses': 0, 'latency_client_total_ms': {'policy_block_fp': {'n': 568, 'min': 3.1492, 'p50': 6.8878, 'p90': 9.1332, 'p99': 9.9046, 'p999': 10.3598, 'max': 10.3598, 'mean': 6.6678}}, 'block_rule': {'statuses': [400, 403, 422, 451], 'envelope_re': '(?i)content_filter|content_polic|polic(y|ies)_|blocked|block_|guard|violation|unsafe|threat'}}) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| infra_error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 29400 over 300 s = 98.0 req/s (configured 98.0)
- qualified: 28832 (96.11 req/s; dispositions ['ALLOW', 'FLAG', 'REDACT'], source auto)
- infra errors (the error-budget gate): 0 (rate 0.0); reasons: {}
- policy stratum (not qualified, not infra errors): expected blocks 0, FALSE-POSITIVE blocks 568 of 29400 benign (FP rate 0.019319727891156463), other blocks 0, policy misses 0
- policy cohort latency policy_block_fp (client total ms): n=568 p50=6.8878 p90=9.1332 p99=9.9046 p99.9=10.3598 max=10.3598 mean=6.6678
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=29400 p50=0.0839 p90=0.0933 p99=0.1041 p99.9=0.1232 max=0.1731 mean=0.0835
- join: {'joined': 28832, 'by_nonce_fallback': 0, 'provider_records': 36060, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=28832 p50=6.985 p90=8.3941 p99=10.6274 p99.9=11.998 max=15.1439 mean=6.3745 |
| T_addon_first | n=28832 p50=6.979 p90=8.555 p99=23.7218 p99.9=28.1293 max=46.3806 mean=6.5718 |
| T_release_lag_max | n=28832 p50=44.366 p90=48.4848 p99=67.4406 p99.9=70.7538 max=89.5412 mean=34.1191 |
| T_release_lag_max_arrival | n=28832 p50=26.6717 p90=30.3458 p99=32.9216 p99.9=35.693 max=40.7168 mean=21.6279 |
| T_fw_addon | n=28832 p50=44.366 p90=48.4848 p99=67.4406 p99.9=70.7538 max=89.5412 mean=34.1191 |
| T_fw_addon_sse | n=20184 p50=46.8471 p90=49.8608 p99=67.689 p99.9=84.2221 max=89.5412 mean=45.9689 |
| T_fw_addon_json | n=8648 p50=7.0562 p90=8.5528 p99=10.6955 p99.9=12.6049 max=13.7615 mean=6.4621 |
| client_ttft_sse | n=20184 p50=156.9927 p90=158.5972 p99=174.8001 p99.9=179.5152 max=196.4136 mean=156.6643 |
| provider_sched_err_last | n=28832 p50=0.0428 p90=0.0875 p99=0.1131 p99.9=0.1319 max=0.1726 mean=0.0453 |
| provider_sched_err_max_per_stream | n=28832 p50=0.0891 p90=0.1298 p99=0.166 p99.9=0.1951 max=0.2353 mean=0.0851 |
| provider_write_max | n=28832 p50=0.02 p90=0.0283 p99=0.0382 p99.9=0.0481 max=0.168 mean=0.0197 |

- release lag: 28832 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s1-098/rv-split-lg-1/lg', 'samples': 300, 'busy_max': 10.69029949734822, 'busy_mean': 7.49, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 9.602486776306417, 'busy_p95': 9.373800228636464}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s1-098/rv-split-lg-1/lg', 'scheduled': 36750, 'recorded': 36750, 'interrupted': False}]
- health olg rv-split-lg-1: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 3345882, 'TcpInSegs': 5680893}
- health synthprov rv-split-prov-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 1, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 1, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 5824447, 'TcpInSegs': 5023268}
