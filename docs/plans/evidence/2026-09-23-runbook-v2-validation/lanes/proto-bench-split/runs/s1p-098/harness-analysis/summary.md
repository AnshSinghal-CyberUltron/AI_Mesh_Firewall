# Harness analysis (sut, policy={'blocks_total': 552, 'expected_blocks': 0, 'false_positive_blocks': 552, 'other_blocks': 0, 'benign_offered': 29570, 'false_positive_rate': 0.01866756848156916, 'policy_misses': 0, 'latency_client_total_ms': {'policy_block_fp': {'n': 552, 'min': 3.4144, 'p50': 7.7503, 'p90': 11.5188, 'p99': 18.0905, 'p999': 27.7096, 'max': 27.7096, 'mean': 8.4241}}, 'block_rule': {'statuses': [400, 403, 422, 451], 'envelope_re': '(?i)content_filter|content_polic|polic(y|ies)_|blocked|block_|guard|violation|unsafe|threat'}}) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| infra_error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 29570 over 300 s = 98.57 req/s (configured 98.0)
- qualified: 29018 (96.73 req/s; dispositions ['ALLOW', 'FLAG', 'REDACT'], source auto)
- infra errors (the error-budget gate): 0 (rate 0.0); reasons: {}
- policy stratum (not qualified, not infra errors): expected blocks 0, FALSE-POSITIVE blocks 552 of 29570 benign (FP rate 0.01866756848156916), other blocks 0, policy misses 0
- policy cohort latency policy_block_fp (client total ms): n=552 p50=7.7503 p90=11.5188 p99=18.0905 p99.9=27.7096 max=27.7096 mean=8.4241
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=29570 p50=0.0871 p90=0.0974 p99=0.1092 p99.9=0.1282 max=0.279 mean=0.0856
- join: {'joined': 29018, 'by_nonce_fallback': 0, 'provider_records': 36305, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=29018 p50=7.9059 p90=11.6037 p99=17.1375 p99.9=22.6404 max=34.7416 mean=8.0074 |
| T_addon_first | n=29018 p50=7.9001 p90=11.8362 p99=25.0741 p99.9=33.5571 max=69.1757 mean=8.2247 |
| T_release_lag_max | n=29018 p50=45.2326 p90=51.6476 p99=69.0397 p99.9=77.3073 max=92.0678 mean=35.804 |
| T_release_lag_max_arrival | n=29018 p50=27.8949 p90=33.0246 p99=38.4971 p99.9=44.2714 max=54.7401 mean=23.4039 |
| T_fw_addon | n=29018 p50=45.2326 p90=51.6476 p99=69.0397 p99.9=77.3073 max=92.0678 mean=35.804 |
| T_fw_addon_sse | n=20316 p50=47.7692 p90=53.4605 p99=70.289 p99.9=80.0434 max=92.0678 mean=47.6792 |
| T_fw_addon_json | n=8702 p50=7.9847 p90=11.6539 p99=16.9555 p99.9=22.8679 max=26.7046 mean=8.0798 |
| client_ttft_sse | n=20316 p50=157.9098 p90=161.9889 p99=177.6931 p99.9=185.5372 max=219.2311 mean=158.333 |
| provider_sched_err_last | n=29018 p50=0.0427 p90=0.0878 p99=0.1123 p99.9=0.1303 max=0.1849 mean=0.0454 |
| provider_sched_err_max_per_stream | n=29018 p50=0.0893 p90=0.1302 p99=0.1694 p99.9=0.2038 max=0.6409 mean=0.0857 |
| provider_write_max | n=29018 p50=0.0182 p90=0.024 p99=0.0342 p99.9=0.0446 max=0.0968 mean=0.0176 |

- release lag: 29018 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s1p-098/rv-split-lg-3/lg', 'samples': 300, 'busy_max': 11.594839602800732, 'busy_mean': 8.16, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-b'}]
- provider CPU: {'samples': 430, 'busy_max': 12.878164270055736, 'busy_p95': 9.09537634881925}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s1p-098/rv-split-lg-3/lg', 'scheduled': 36971, 'recorded': 36971, 'interrupted': False}]
- health olg rv-split-lg-3: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 3517167, 'TcpInSegs': 5721575}
- health synthprov rv-split-prov-3: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 27, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 7, 'TcpExtTCPTimeouts': 21, 'TcpOutSegs': 5864440, 'TcpInSegs': 5099359}
