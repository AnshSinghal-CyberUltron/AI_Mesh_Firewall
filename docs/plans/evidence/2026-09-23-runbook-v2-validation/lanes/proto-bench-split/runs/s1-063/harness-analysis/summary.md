# Harness analysis (sut, policy={'blocks_total': 335, 'expected_blocks': 0, 'false_positive_blocks': 335, 'other_blocks': 0, 'benign_offered': 18900, 'false_positive_rate': 0.017724867724867723, 'policy_misses': 0, 'latency_client_total_ms': {'policy_block_fp': {'n': 335, 'min': 3.2336, 'p50': 6.8073, 'p90': 9.0978, 'p99': 9.7865, 'p999': 11.4496, 'max': 11.4496, 'mean': 6.6213}}, 'block_rule': {'statuses': [400, 403, 422, 451], 'envelope_re': '(?i)content_filter|content_polic|polic(y|ies)_|blocked|block_|guard|violation|unsafe|threat'}}) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| infra_error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 18900 over 300 s = 63.0 req/s (configured 63.0)
- qualified: 18565 (61.88 req/s; dispositions ['ALLOW', 'FLAG', 'REDACT'], source auto)
- infra errors (the error-budget gate): 0 (rate 0.0); reasons: {}
- policy stratum (not qualified, not infra errors): expected blocks 0, FALSE-POSITIVE blocks 335 of 18900 benign (FP rate 0.017724867724867723), other blocks 0, policy misses 0
- policy cohort latency policy_block_fp (client total ms): n=335 p50=6.8073 p90=9.0978 p99=9.7865 p99.9=11.4496 max=11.4496 mean=6.6213
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=18900 p50=0.0838 p90=0.0935 p99=0.1042 p99.9=0.117 max=0.3368 mean=0.0836
- join: {'joined': 18565, 'by_nonce_fallback': 0, 'provider_records': 23224, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=18565 p50=6.9306 p90=7.9743 p99=10.2385 p99.9=11.4469 max=13.4019 mean=6.2417 |
| T_addon_first | n=18565 p50=6.9226 p90=8.0419 p99=23.5407 p99.9=29.6438 max=47.8046 mean=6.4248 |
| T_release_lag_max | n=18565 p50=44.286 p90=48.0369 p99=67.3553 p99.9=71.5425 max=107.1631 mean=34.0445 |
| T_release_lag_max_arrival | n=18565 p50=26.3354 p90=29.9727 p99=32.448 p99.9=33.7123 max=36.1927 mean=21.2951 |
| T_fw_addon | n=18565 p50=44.286 p90=48.0369 p99=67.3553 p99.9=71.5425 max=107.1631 mean=34.0445 |
| T_fw_addon_sse | n=12998 p50=46.8031 p90=49.7614 p99=67.5723 p99.9=84.529 max=107.1631 mean=45.9267 |
| T_fw_addon_json | n=5567 p50=6.9758 p90=8.0521 p99=10.2739 p99.9=11.3104 max=12.3587 mean=6.3014 |
| client_ttft_sse | n=12998 p50=156.9463 p90=158.0799 p99=174.5313 p99.9=179.8637 max=197.8368 mean=156.5253 |
| provider_sched_err_last | n=18565 p50=0.0466 p90=0.0888 p99=0.1089 p99.9=0.1282 max=0.1549 mean=0.0479 |
| provider_sched_err_max_per_stream | n=18565 p50=0.0824 p90=0.1232 p99=0.156 p99.9=0.1958 max=0.8671 mean=0.0805 |
| provider_write_max | n=18565 p50=0.0196 p90=0.0254 p99=0.0361 p99.9=0.0471 max=0.0747 mean=0.0188 |

- release lag: 18565 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s1-063/rv-split-lg-1/lg', 'samples': 300, 'busy_max': 14.533668111674093, 'busy_mean': 5.36, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 19.78454901675306, 'busy_p95': 7.461494528610424}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s1-063/rv-split-lg-1/lg', 'scheduled': 23625, 'recorded': 23625, 'interrupted': False}]
- health olg rv-split-lg-1: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 2207331, 'TcpInSegs': 3660860}
- health synthprov rv-split-prov-1: gc_cycles=0 sched_latency_max_ms=0.16384 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 3753971, 'TcpInSegs': 3283054}
