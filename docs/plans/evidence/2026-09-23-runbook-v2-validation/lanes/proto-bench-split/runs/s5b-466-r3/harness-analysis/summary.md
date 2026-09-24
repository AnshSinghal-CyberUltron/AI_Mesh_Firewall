# Harness analysis (sut, policy={'blocks_total': 2422, 'expected_blocks': 0, 'false_positive_blocks': 2422, 'other_blocks': 0, 'benign_offered': 139800, 'false_positive_rate': 0.017324749642346208, 'policy_misses': 0, 'latency_client_total_ms': {'policy_block_fp': {'n': 2422, 'min': 3.2939, 'p50': 7.5245, 'p90': 10.0177, 'p99': 14.2646, 'p999': 20.311, 'max': 23.4745, 'mean': 7.6418}}, 'block_rule': {'statuses': [400, 403, 422, 451], 'envelope_re': '(?i)content_filter|content_polic|polic(y|ies)_|blocked|block_|guard|violation|unsafe|threat'}}) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| infra_error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 139800 over 300 s = 466.0 req/s (configured 466.0)
- qualified: 137378 (457.93 req/s; dispositions ['ALLOW', 'FLAG', 'REDACT'], source auto)
- infra errors (the error-budget gate): 0 (rate 0.0); reasons: {}
- policy stratum (not qualified, not infra errors): expected blocks 0, FALSE-POSITIVE blocks 2422 of 139800 benign (FP rate 0.017324749642346208), other blocks 0, policy misses 0
- policy cohort latency policy_block_fp (client total ms): n=2422 p50=7.5245 p90=10.0177 p99=14.2646 p99.9=20.311 max=23.4745 mean=7.6418
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=139800 p50=0.0853 p90=0.097 p99=0.1112 p99.9=0.133 max=0.5917 mean=0.085
- join: {'joined': 137378, 'by_nonce_fallback': 0, 'provider_records': 171730, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=137378 p50=7.96 p90=10.8663 p99=15.2184 p99.9=19.1803 max=112.8456 mean=7.7288 |
| T_addon_first | n=137378 p50=7.9518 p90=10.9288 p99=24.8696 p99.9=31.013 max=112.8456 mean=7.9456 |
| T_release_lag_max | n=137378 p50=45.231 p90=50.8939 p99=69.6915 p99.9=90.8545 max=149.1584 mean=35.6851 |
| T_release_lag_max_arrival | n=137378 p50=28.7654 p90=33.4863 p99=38.4798 p99.9=89.5536 max=137.1 mean=23.8581 |
| T_fw_addon | n=137378 p50=45.231 p90=50.8939 p99=69.6915 p99.9=90.8545 max=149.1584 mean=35.6851 |
| T_fw_addon_sse | n=96203 p50=47.8412 p90=52.1856 p99=70.4892 p99.9=95.6248 max=149.1584 mean=47.6166 |
| T_fw_addon_json | n=41175 p50=8.025 p90=10.9498 p99=15.3357 p99.9=19.3544 max=112.8456 mean=7.8078 |
| client_ttft_sse | n=96203 p50=157.9618 p90=160.9627 p99=177.2565 p99.9=182.0772 max=259.336 mean=158.045 |
| provider_sched_err_last | n=137378 p50=0.0326 p90=0.0857 p99=0.1184 p99.9=0.1507 max=0.2129 mean=0.0399 |
| provider_sched_err_max_per_stream | n=137378 p50=0.1095 p90=0.1536 p99=0.1959 p99.9=0.2313 max=0.5973 mean=0.0993 |
| provider_write_max | n=137378 p50=0.0209 p90=0.0289 p99=0.0397 p99.9=0.0505 max=0.6134 mean=0.0205 |

- release lag: 137378 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s5b-466-r3/rv-split-lg-1/lg', 'samples': 300, 'busy_max': 25.354160782293512, 'busy_mean': 10.46, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-b'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s5b-466-r3/rv-split-lg-2/lg', 'samples': 300, 'busy_max': 28.050733548165407, 'busy_mean': 15.77, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-b'}]
- provider CPU: {'samples': 868, 'busy_max': 31.230209317734538, 'busy_p95': 18.16898148153704}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s5b-466-r3/rv-split-lg-1/lg', 'scheduled': 87375, 'recorded': 87375, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s5b-466-r3/rv-split-lg-2/lg', 'scheduled': 87375, 'recorded': 87375, 'interrupted': False}]
- health olg rv-split-lg-1: gc_cycles=0 sched_latency_max_ms=0.13107200000000002 tcp={'TcpRetransSegs': 2, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 2, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 7832196, 'TcpInSegs': 13634233}
- health olg rv-split-lg-2: gc_cycles=0 sched_latency_max_ms=0.13107200000000002 tcp={'TcpRetransSegs': 24, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 8, 'TcpExtTCPTimeouts': 20, 'TcpOutSegs': 7846415, 'TcpInSegs': 13632576}
- health synthprov rv-split-prov-1: gc_cycles=0 sched_latency_max_ms=0.16384 tcp={'TcpRetransSegs': 59, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 59, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 18558684, 'TcpInSegs': 16343165}
- health synthprov rv-split-prov-2: gc_cycles=0 sched_latency_max_ms=0.13107200000000002 tcp={'TcpRetransSegs': 14, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 14, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 9264711, 'TcpInSegs': 8141610}
