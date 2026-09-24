# Harness analysis (sut, policy={'blocks_total': 421, 'expected_blocks': 0, 'false_positive_blocks': 421, 'other_blocks': 0, 'benign_offered': 23400, 'false_positive_rate': 0.01799145299145299, 'policy_misses': 0, 'latency_client_total_ms': {'policy_block_fp': {'n': 421, 'min': 4.1323, 'p50': 9.6678, 'p90': 12.3567, 'p99': 14.1073, 'p999': 18.0169, 'max': 18.0169, 'mean': 9.4323}}, 'block_rule': {'statuses': [400, 403, 422, 451], 'envelope_re': '(?i)content_filter|content_polic|polic(y|ies)_|blocked|block_|guard|violation|unsafe|threat'}}) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| infra_error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 23400 over 300 s = 78.0 req/s (configured 78.0)
- qualified: 22959 (76.53 req/s; dispositions ['ALLOW', 'FLAG', 'REDACT'], source auto)
- infra errors (the error-budget gate): 20 (rate 0.0008547008547008547); reasons: {'http_503': 20, 'incomplete': 20, 'unjoined': 20, 'disposition_missing': 20, 'stage_canon_missing': 20, 'stage_det_missing': 20, 'stage_sem_missing': 20, 'stage_resolve_missing': 20, 'stage_dispatch_missing': 20, 'stage_out_missing': 20, 'stage_audit_missing': 20}
- policy stratum (not qualified, not infra errors): expected blocks 0, FALSE-POSITIVE blocks 421 of 23400 benign (FP rate 0.01799145299145299), other blocks 0, policy misses 0
- policy cohort latency policy_block_fp (client total ms): n=421 p50=9.6678 p90=12.3567 p99=14.1073 p99.9=18.0169 max=18.0169 mean=9.4323
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=23400 p50=0.0844 p90=0.0941 p99=0.1055 p99.9=0.1205 max=0.1546 mean=0.0843
- join: {'joined': 22959, 'by_nonce_fallback': 0, 'provider_records': 28734, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=22959 p50=9.6028 p90=12.404 p99=15.7004 p99.9=20.3508 max=28.3323 mean=9.0324 |
| T_addon_first | n=22959 p50=9.5501 p90=12.7818 p99=25.36 p99.9=33.5766 max=50.876 mean=9.1945 |
| T_release_lag_max | n=2252 p50=46.153 p90=52.9677 p99=70.523 p99.9=73.9943 max=89.4092 mean=37.0869 |
| T_release_lag_max_arrival | n=2252 p50=32.516 p90=38.0773 p99=41.6372 p99.9=46.3401 max=53.7925 mean=26.9611 |
| T_fw_addon | n=22959 p50=9.8339 p90=14.4737 p99=52.8792 p99.9=70.523 max=89.4092 mean=12.0377 |
| T_fw_addon_sse | n=16072 p50=9.9178 p90=30.9776 p99=53.8681 p99.9=70.8255 max=89.4092 mean=13.2942 |
| T_fw_addon_json | n=6887 p50=9.6336 p90=12.7262 p99=15.7004 p99.9=20.7805 max=23.0659 mean=9.1057 |
| client_ttft_sse | n=16072 p50=159.5579 p90=162.8444 p99=178.82 p99.9=183.963 max=200.9067 mean=159.2807 |
| provider_sched_err_last | n=22959 p50=0.0469 p90=0.0874 p99=0.1093 p99.9=0.1248 max=0.1538 mean=0.0478 |
| provider_sched_err_max_per_stream | n=22959 p50=0.0846 p90=0.1244 p99=0.1602 p99.9=0.1956 max=0.3369 mean=0.0822 |
| provider_write_max | n=22959 p50=0.0165 p90=0.0229 p99=0.0326 p99.9=0.0424 max=0.0533 mean=0.0162 |

- release lag: 2252 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22-078/rv-pbu-lg-1/lg', 'samples': 300, 'busy_max': 17.120187032504585, 'busy_mean': 5.04, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22-078/rv-pbu-lg-2/lg', 'samples': 300, 'busy_max': 17.078769842654406, 'busy_mean': 4.85, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 21.86424427435081, 'busy_p95': 8.00987617428629}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22-078/rv-pbu-lg-1/lg', 'scheduled': 14625, 'recorded': 14625, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22-078/rv-pbu-lg-2/lg', 'scheduled': 14625, 'recorded': 14625, 'interrupted': False}]
- health olg rv-pbu-lg-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 1, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 1, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1429803, 'TcpInSegs': 2254731}
- health olg rv-pbu-lg-2: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 4, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 4, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1431372, 'TcpInSegs': 2252959}
- health synthprov rv-pbu-prov-1: gc_cycles=0 sched_latency_max_ms=0.196608 tcp={'TcpRetransSegs': 26, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 26, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 4626790, 'TcpInSegs': 3278254}
