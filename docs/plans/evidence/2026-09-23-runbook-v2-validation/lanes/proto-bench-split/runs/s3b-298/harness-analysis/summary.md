# Harness analysis (sut, policy={'blocks_total': 1612, 'expected_blocks': 0, 'false_positive_blocks': 1612, 'other_blocks': 0, 'benign_offered': 89400, 'false_positive_rate': 0.018031319910514543, 'policy_misses': 0, 'latency_client_total_ms': {'policy_block_fp': {'n': 1612, 'min': 3.386, 'p50': 7.6347, 'p90': 10.5367, 'p99': 15.0634, 'p999': 20.5419, 'max': 21.2807, 'mean': 7.9383}}, 'block_rule': {'statuses': [400, 403, 422, 451], 'envelope_re': '(?i)content_filter|content_polic|polic(y|ies)_|blocked|block_|guard|violation|unsafe|threat'}}) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| infra_error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 89400 over 300 s = 298.0 req/s (configured 298.0)
- qualified: 87788 (292.63 req/s; dispositions ['ALLOW', 'FLAG', 'REDACT'], source auto)
- infra errors (the error-budget gate): 0 (rate 0.0); reasons: {}
- policy stratum (not qualified, not infra errors): expected blocks 0, FALSE-POSITIVE blocks 1612 of 89400 benign (FP rate 0.018031319910514543), other blocks 0, policy misses 0
- policy cohort latency policy_block_fp (client total ms): n=1612 p50=7.6347 p90=10.5367 p99=15.0634 p99.9=20.5419 max=21.2807 mean=7.9383
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=89400 p50=0.0853 p90=0.0964 p99=0.1091 p99.9=0.1289 max=1.2222 mean=0.0852
- join: {'joined': 87788, 'by_nonce_fallback': 0, 'provider_records': 109772, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=87788 p50=7.9809 p90=10.9375 p99=15.3607 p99.9=19.7951 max=26.916 mean=7.8231 |
| T_addon_first | n=87788 p50=7.9749 p90=11.0493 p99=24.7312 p99.9=31.5588 max=73.6205 mean=8.041 |
| T_release_lag_max | n=87788 p50=45.1906 p90=50.9657 p99=69.1352 p99.9=76.7641 max=109.5045 mean=35.6581 |
| T_release_lag_max_arrival | n=87788 p50=28.6263 p90=33.3012 p99=37.946 p99.9=44.0461 max=70.3684 mean=23.7017 |
| T_fw_addon | n=87788 p50=45.1906 p90=50.9657 p99=69.1352 p99.9=76.7641 max=109.5045 mean=35.6581 |
| T_fw_addon_sse | n=61485 p50=47.8571 p90=52.543 p99=70.0266 p99.9=84.5004 max=109.5045 mean=47.5289 |
| T_fw_addon_json | n=26303 p50=8.053 p90=11.011 p99=15.5109 p99.9=20.0962 max=24.8404 mean=7.9094 |
| client_ttft_sse | n=61485 p50=157.9859 p90=161.1137 p99=177.3574 p99.9=183.3033 max=223.6236 mean=158.1411 |
| provider_sched_err_last | n=87788 p50=0.0394 p90=0.086 p99=0.1132 p99.9=0.1371 max=0.2068 mean=0.0432 |
| provider_sched_err_max_per_stream | n=87788 p50=0.096 p90=0.1372 p99=0.1796 p99.9=0.2093 max=0.2909 mean=0.0903 |
| provider_write_max | n=87788 p50=0.0202 p90=0.0265 p99=0.0369 p99.9=0.0464 max=0.2989 mean=0.0194 |

- release lag: 87788 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s3b-298/rv-split-lg-1/lg', 'samples': 300, 'busy_max': 11.402905558328314, 'busy_mean': 7.65, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-b'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s3b-298/rv-split-lg-2/lg', 'samples': 300, 'busy_max': 11.65046251263867, 'busy_mean': 10.84, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-b'}]
- provider CPU: {'samples': 866, 'busy_max': 11.754100648407462, 'busy_p95': 11.435903660843739}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s3b-298/rv-split-lg-1/lg', 'scheduled': 55875, 'recorded': 55875, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s3b-298/rv-split-lg-2/lg', 'scheduled': 55875, 'recorded': 55875, 'interrupted': False}]
- health olg rv-split-lg-1: gc_cycles=0 sched_latency_max_ms=0.08192 tcp={'TcpRetransSegs': 1, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 1, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 5084317, 'TcpInSegs': 8715278}
- health olg rv-split-lg-2: gc_cycles=0 sched_latency_max_ms=0.13107200000000002 tcp={'TcpRetransSegs': 64, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 5, 'TcpExtTCPTimeouts': 66, 'TcpOutSegs': 5079882, 'TcpInSegs': 8710986}
- health synthprov rv-split-prov-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 15, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 10, 'TcpExtTCPTimeouts': 6, 'TcpOutSegs': 8891490, 'TcpInSegs': 7532129}
- health synthprov rv-split-prov-2: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 17, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 11, 'TcpExtTCPTimeouts': 6, 'TcpOutSegs': 8889705, 'TcpInSegs': 7533114}
