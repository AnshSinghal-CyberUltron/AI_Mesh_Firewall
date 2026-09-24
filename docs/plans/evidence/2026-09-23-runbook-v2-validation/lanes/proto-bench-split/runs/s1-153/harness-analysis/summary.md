# Harness analysis (sut, policy={'blocks_total': 829, 'expected_blocks': 0, 'false_positive_blocks': 829, 'other_blocks': 0, 'benign_offered': 45900, 'false_positive_rate': 0.018061002178649237, 'policy_misses': 0, 'latency_client_total_ms': {'policy_block_fp': {'n': 829, 'min': 3.2027, 'p50': 6.8598, 'p90': 9.2528, 'p99': 9.9748, 'p999': 10.5157, 'max': 10.5157, 'mean': 6.6912}}, 'block_rule': {'statuses': [400, 403, 422, 451], 'envelope_re': '(?i)content_filter|content_polic|polic(y|ies)_|blocked|block_|guard|violation|unsafe|threat'}}) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| infra_error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 45900 over 300 s = 153.0 req/s (configured 153.0)
- qualified: 45071 (150.24 req/s; dispositions ['ALLOW', 'FLAG', 'REDACT'], source auto)
- infra errors (the error-budget gate): 0 (rate 0.0); reasons: {}
- policy stratum (not qualified, not infra errors): expected blocks 0, FALSE-POSITIVE blocks 829 of 45900 benign (FP rate 0.018061002178649237), other blocks 0, policy misses 0
- policy cohort latency policy_block_fp (client total ms): n=829 p50=6.8598 p90=9.2528 p99=9.9748 p99.9=10.5157 max=10.5157 mean=6.6912
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=45900 p50=0.084 p90=0.094 p99=0.1063 p99.9=0.1212 max=0.2128 mean=0.0835
- join: {'joined': 45071, 'by_nonce_fallback': 0, 'provider_records': 56352, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=45071 p50=6.9691 p90=8.7229 p99=10.4941 p99.9=12.1629 max=21.4923 mean=6.4057 |
| T_addon_first | n=45071 p50=6.9652 p90=9.2948 p99=24.0607 p99.9=29.8081 max=50.0806 mean=6.6287 |
| T_release_lag_max | n=45071 p50=44.3937 p90=48.948 p99=67.5402 p99.9=70.4152 max=89.8054 mean=34.2158 |
| T_release_lag_max_arrival | n=45071 p50=26.806 p90=30.5467 p99=33.0058 p99.9=34.6314 max=45.0226 mean=21.7776 |
| T_fw_addon | n=45071 p50=44.3937 p90=48.948 p99=67.5402 p99.9=70.4152 max=89.8054 mean=34.2158 |
| T_fw_addon_sse | n=31566 p50=46.8402 p90=49.9047 p99=67.7809 p99.9=84.288 max=89.8054 mean=46.0794 |
| T_fw_addon_json | n=13505 p50=7.034 p90=8.9609 p99=10.546 p99.9=12.45 max=21.4923 mean=6.4863 |
| client_ttft_sse | n=31566 p50=156.9778 p90=159.4276 p99=176.3715 p99.9=180.0217 max=200.1621 mean=156.7325 |
| provider_sched_err_last | n=45071 p50=0.0372 p90=0.0865 p99=0.1135 p99.9=0.1374 max=0.2086 mean=0.0421 |
| provider_sched_err_max_per_stream | n=45071 p50=0.0964 p90=0.1375 p99=0.176 p99.9=0.2078 max=0.5429 mean=0.0901 |
| provider_write_max | n=45071 p50=0.02 p90=0.0272 p99=0.0389 p99.9=0.0498 max=0.1174 mean=0.0196 |

- release lag: 45071 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s1-153/rv-split-lg-1/lg', 'samples': 300, 'busy_max': 20.43969525104282, 'busy_mean': 10.92, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 25.0996428629352, 'busy_p95': 11.84026739628895}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s1-153/rv-split-lg-1/lg', 'scheduled': 57375, 'recorded': 57375, 'interrupted': False}]
- health olg rv-split-lg-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 1, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 1, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 5154206, 'TcpInSegs': 8903795}
- health synthprov rv-split-prov-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 7, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 2, 'TcpExtTCPTimeouts': 6, 'TcpOutSegs': 9129265, 'TcpInSegs': 7670079}
