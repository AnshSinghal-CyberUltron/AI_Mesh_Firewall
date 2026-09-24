# Harness analysis (sut, policy={'blocks_total': 517, 'expected_blocks': 0, 'false_positive_blocks': 517, 'other_blocks': 0, 'benign_offered': 29570, 'false_positive_rate': 0.017483936422049373, 'policy_misses': 0, 'latency_client_total_ms': {'policy_block_fp': {'n': 517, 'min': 3.4557, 'p50': 7.744, 'p90': 11.5983, 'p99': 18.7389, 'p999': 27.7909, 'max': 27.7909, 'mean': 8.4735}}, 'block_rule': {'statuses': [400, 403, 422, 451], 'envelope_re': '(?i)content_filter|content_polic|polic(y|ies)_|blocked|block_|guard|violation|unsafe|threat'}}) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| infra_error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 29570 over 300 s = 98.57 req/s (configured 98.0)
- qualified: 29053 (96.84 req/s; dispositions ['ALLOW', 'FLAG', 'REDACT'], source auto)
- infra errors (the error-budget gate): 0 (rate 0.0); reasons: {}
- policy stratum (not qualified, not infra errors): expected blocks 0, FALSE-POSITIVE blocks 517 of 29570 benign (FP rate 0.017483936422049373), other blocks 0, policy misses 0
- policy cohort latency policy_block_fp (client total ms): n=517 p50=7.744 p90=11.5983 p99=18.7389 p99.9=27.7909 max=27.7909 mean=8.4735
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=29570 p50=0.0866 p90=0.0969 p99=0.1089 p99.9=0.1263 max=0.1617 mean=0.0852
- join: {'joined': 29053, 'by_nonce_fallback': 0, 'provider_records': 36349, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=29053 p50=7.8524 p90=11.534 p99=17.0641 p99.9=22.9702 max=31.1888 mean=7.9502 |
| T_addon_first | n=29053 p50=7.844 p90=11.7393 p99=24.4939 p99.9=32.1872 max=67.9139 mean=8.1325 |
| T_release_lag_max | n=29053 p50=45.178 p90=51.5505 p99=69.6177 p99.9=76.8094 max=92.6666 mean=35.7801 |
| T_release_lag_max_arrival | n=29053 p50=27.895 p90=32.9876 p99=38.4688 p99.9=43.9976 max=52.6157 mean=23.3741 |
| T_fw_addon | n=29053 p50=45.178 p90=51.5505 p99=69.6177 p99.9=76.8094 max=92.6666 mean=35.7801 |
| T_fw_addon_sse | n=20345 p50=47.7185 p90=53.2278 p99=70.6345 p99.9=80.004 max=92.6666 mean=47.6584 |
| T_fw_addon_json | n=8708 p50=7.9312 p90=11.6401 p99=16.8095 p99.9=22.1588 max=28.646 mean=8.0281 |
| client_ttft_sse | n=20345 p50=157.8503 p90=161.8396 p99=176.6154 p99.9=185.2525 max=217.9551 mean=158.2234 |
| provider_sched_err_last | n=29053 p50=0.043 p90=0.0874 p99=0.1111 p99.9=0.1311 max=0.1926 mean=0.0456 |
| provider_sched_err_max_per_stream | n=29053 p50=0.0892 p90=0.1295 p99=0.1683 p99.9=0.2024 max=0.6099 mean=0.0852 |
| provider_write_max | n=29053 p50=0.0183 p90=0.0242 p99=0.0344 p99.9=0.0455 max=0.1696 mean=0.0177 |

- release lag: 29053 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s1p-098-r2/rv-split-lg-3/lg', 'samples': 300, 'busy_max': 11.531141234492582, 'busy_mean': 7.92, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-b'}]
- provider CPU: {'samples': 430, 'busy_max': 15.350624639637322, 'busy_p95': 9.045958665473252}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s1p-098-r2/rv-split-lg-3/lg', 'scheduled': 36971, 'recorded': 36971, 'interrupted': False}]
- health olg rv-split-lg-3: gc_cycles=0 sched_latency_max_ms=0.08192 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 3507585, 'TcpInSegs': 5728474}
- health synthprov rv-split-prov-3: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 16, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 4, 'TcpExtTCPTimeouts': 11, 'TcpOutSegs': 5871626, 'TcpInSegs': 5111535}
