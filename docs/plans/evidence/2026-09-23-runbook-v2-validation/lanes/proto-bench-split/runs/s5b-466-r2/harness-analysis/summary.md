# Harness analysis (sut, policy={'blocks_total': 2444, 'expected_blocks': 0, 'false_positive_blocks': 2444, 'other_blocks': 0, 'benign_offered': 139800, 'false_positive_rate': 0.017482117310443492, 'policy_misses': 0, 'latency_client_total_ms': {'policy_block_fp': {'n': 2444, 'min': 3.3158, 'p50': 7.5001, 'p90': 10.0543, 'p99': 14.423, 'p999': 18.9384, 'max': 20.8263, 'mean': 7.6748}}, 'block_rule': {'statuses': [400, 403, 422, 451], 'envelope_re': '(?i)content_filter|content_polic|polic(y|ies)_|blocked|block_|guard|violation|unsafe|threat'}}) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| infra_error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 139800 over 300 s = 466.0 req/s (configured 466.0)
- qualified: 137356 (457.85 req/s; dispositions ['ALLOW', 'FLAG', 'REDACT'], source auto)
- infra errors (the error-budget gate): 0 (rate 0.0); reasons: {}
- policy stratum (not qualified, not infra errors): expected blocks 0, FALSE-POSITIVE blocks 2444 of 139800 benign (FP rate 0.017482117310443492), other blocks 0, policy misses 0
- policy cohort latency policy_block_fp (client total ms): n=2444 p50=7.5001 p90=10.0543 p99=14.423 p99.9=18.9384 max=20.8263 mean=7.6748
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=139800 p50=0.0852 p90=0.0968 p99=0.1111 p99.9=0.1315 max=0.5295 mean=0.0848
- join: {'joined': 137356, 'by_nonce_fallback': 0, 'provider_records': 171702, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=137356 p50=7.9225 p90=10.8265 p99=15.0575 p99.9=19.1565 max=34.0676 mean=7.6795 |
| T_addon_first | n=137356 p50=7.9113 p90=10.8964 p99=24.9195 p99.9=31.1543 max=68.1351 mean=7.896 |
| T_release_lag_max | n=137356 p50=45.1981 p90=50.8359 p99=68.9059 p99.9=75.0173 max=112.4993 mean=35.5717 |
| T_release_lag_max_arrival | n=137356 p50=28.6773 p90=33.3083 p99=37.835 p99.9=43.8452 max=68.9581 mean=23.6624 |
| T_fw_addon | n=137356 p50=45.1981 p90=50.8359 p99=68.9059 p99.9=75.0173 max=112.4993 mean=35.5717 |
| T_fw_addon_sse | n=96184 p50=47.7931 p90=52.0667 p99=70.0746 p99.9=78.7678 max=112.4993 mean=47.4769 |
| T_fw_addon_json | n=41172 p50=7.9969 p90=10.9148 p99=15.1209 p99.9=19.0611 max=34.0676 mean=7.7592 |
| client_ttft_sse | n=96184 p50=157.9167 p90=160.9264 p99=177.6209 p99.9=182.402 max=218.2394 mean=157.9951 |
| provider_sched_err_last | n=137356 p50=0.033 p90=0.0857 p99=0.1188 p99.9=0.1499 max=0.2948 mean=0.0401 |
| provider_sched_err_max_per_stream | n=137356 p50=0.1094 p90=0.1535 p99=0.196 p99.9=0.2258 max=0.4507 mean=0.0992 |
| provider_write_max | n=137356 p50=0.021 p90=0.0289 p99=0.0397 p99.9=0.0508 max=0.3082 mean=0.0206 |

- release lag: 137356 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s5b-466-r2/rv-split-lg-1/lg', 'samples': 300, 'busy_max': 14.533887654200573, 'busy_mean': 10.37, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-b'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s5b-466-r2/rv-split-lg-2/lg', 'samples': 300, 'busy_max': 16.461651103331953, 'busy_mean': 15.72, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-b'}]
- provider CPU: {'samples': 850, 'busy_max': 31.250946595919892, 'busy_p95': 18.18632490462011}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s5b-466-r2/rv-split-lg-1/lg', 'scheduled': 87375, 'recorded': 87375, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s5b-466-r2/rv-split-lg-2/lg', 'scheduled': 87375, 'recorded': 87375, 'interrupted': False}]
- health olg rv-split-lg-1: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 1, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 1, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 7824523, 'TcpInSegs': 13626690}
- health olg rv-split-lg-2: gc_cycles=0 sched_latency_max_ms=0.08192 tcp={'TcpRetransSegs': 15, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 4, 'TcpExtTCPTimeouts': 6, 'TcpOutSegs': 7835102, 'TcpInSegs': 13635054}
- health synthprov rv-split-prov-1: gc_cycles=0 sched_latency_max_ms=0.13107200000000002 tcp={'TcpRetransSegs': 35, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 35, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 18557295, 'TcpInSegs': 16356325}
- health synthprov rv-split-prov-2: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 13, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 13, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 9260919, 'TcpInSegs': 8154107}
