# Harness analysis (sut, policy={'blocks_total': 1587, 'expected_blocks': 0, 'false_positive_blocks': 1587, 'other_blocks': 0, 'benign_offered': 89400, 'false_positive_rate': 0.017751677852348994, 'policy_misses': 0, 'latency_client_total_ms': {'policy_block_fp': {'n': 1587, 'min': 3.285, 'p50': 7.6722, 'p90': 10.237, 'p99': 14.9102, 'p999': 18.094, 'max': 19.7379, 'mean': 7.9072}}, 'block_rule': {'statuses': [400, 403, 422, 451], 'envelope_re': '(?i)content_filter|content_polic|polic(y|ies)_|blocked|block_|guard|violation|unsafe|threat'}}) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| infra_error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 89400 over 300 s = 298.0 req/s (configured 298.0)
- qualified: 87813 (292.71 req/s; dispositions ['ALLOW', 'FLAG', 'REDACT'], source auto)
- infra errors (the error-budget gate): 0 (rate 0.0); reasons: {}
- policy stratum (not qualified, not infra errors): expected blocks 0, FALSE-POSITIVE blocks 1587 of 89400 benign (FP rate 0.017751677852348994), other blocks 0, policy misses 0
- policy cohort latency policy_block_fp (client total ms): n=1587 p50=7.6722 p90=10.237 p99=14.9102 p99.9=18.094 max=19.7379 mean=7.9072
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=89400 p50=0.0856 p90=0.0966 p99=0.1091 p99.9=0.1288 max=0.5358 mean=0.0855
- join: {'joined': 87813, 'by_nonce_fallback': 0, 'provider_records': 109803, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=87813 p50=8.0087 p90=10.9873 p99=15.4773 p99.9=19.7133 max=31.9027 mean=7.846 |
| T_addon_first | n=87813 p50=7.9988 p90=11.0922 p99=24.7555 p99.9=31.7235 max=68.2392 mean=8.0622 |
| T_release_lag_max | n=87813 p50=45.2017 p90=51.0248 p99=69.3023 p99.9=76.4583 max=108.472 mean=35.7255 |
| T_release_lag_max_arrival | n=87813 p50=28.6862 p90=33.4375 p99=38.3035 p99.9=44.3472 max=54.1645 mean=23.7731 |
| T_fw_addon | n=87813 p50=45.2017 p90=51.0248 p99=69.3023 p99.9=76.4583 max=108.472 mean=35.7255 |
| T_fw_addon_sse | n=61506 p50=47.8816 p90=52.6771 p99=70.1521 p99.9=84.4313 max=108.472 mean=47.6112 |
| T_fw_addon_json | n=26307 p50=8.0813 p90=11.069 p99=15.5565 p99.9=19.3931 max=31.9027 mean=7.9367 |
| client_ttft_sse | n=61506 p50=158.0104 p90=161.1512 p99=177.3153 p99.9=183.2928 max=218.334 mean=158.1593 |
| provider_sched_err_last | n=87813 p50=0.0388 p90=0.0857 p99=0.113 p99.9=0.1338 max=0.2122 mean=0.0429 |
| provider_sched_err_max_per_stream | n=87813 p50=0.0962 p90=0.1369 p99=0.179 p99.9=0.2104 max=0.4764 mean=0.0902 |
| provider_write_max | n=87813 p50=0.0201 p90=0.0267 p99=0.0374 p99.9=0.0489 max=0.2195 mean=0.0195 |

- release lag: 87813 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s3b-298-r2/rv-split-lg-1/lg', 'samples': 300, 'busy_max': 10.836188729749052, 'busy_mean': 7.2, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-b'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s3b-298-r2/rv-split-lg-2/lg', 'samples': 300, 'busy_max': 11.549804360075111, 'busy_mean': 10.95, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-b'}]
- provider CPU: {'samples': 866, 'busy_max': 24.623273209017082, 'busy_p95': 11.471991745410815}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s3b-298-r2/rv-split-lg-1/lg', 'scheduled': 55875, 'recorded': 55875, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s3b-298-r2/rv-split-lg-2/lg', 'scheduled': 55875, 'recorded': 55875, 'interrupted': False}]
- health olg rv-split-lg-1: gc_cycles=0 sched_latency_max_ms=0.08192 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 5086605, 'TcpInSegs': 8719295}
- health olg rv-split-lg-2: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 75, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 4, 'TcpExtTCPTimeouts': 81, 'TcpOutSegs': 5061478, 'TcpInSegs': 8711384}
- health synthprov rv-split-prov-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 17, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 17, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 8896169, 'TcpInSegs': 7537075}
- health synthprov rv-split-prov-2: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 11, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 11, 'TcpExtTCPTimeouts': 1, 'TcpOutSegs': 8890497, 'TcpInSegs': 7514028}
