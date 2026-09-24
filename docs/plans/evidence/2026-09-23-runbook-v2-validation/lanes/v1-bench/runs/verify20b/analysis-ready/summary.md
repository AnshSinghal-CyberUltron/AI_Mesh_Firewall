# Harness analysis (sut, policy={'blocks_total': 0, 'expected_blocks': 0, 'false_positive_blocks': 0, 'other_blocks': 0, 'benign_offered': 20, 'false_positive_rate': 0.0, 'policy_misses': 0, 'latency_client_total_ms': {}, 'block_rule': {'statuses': [400, 403, 422, 451], 'envelope_re': '(?i)content_filter|content_polic|polic(y|ies)_|blocked|block_|guard|violation|unsafe|threat'}}) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| infra_error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 20 over 10 s = 2.0 req/s (configured 2.0)
- qualified: 20 (2.0 req/s; dispositions ['ALLOW', 'FLAG', 'REDACT'], source v1)
- infra errors (the error-budget gate): 0 (rate 0.0); reasons: {}
- policy stratum (not qualified, not infra errors): expected blocks 0, FALSE-POSITIVE blocks 0 of 20 benign (FP rate 0.0), other blocks 0, policy misses 0
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=20 p50=0.1322 p90=0.1961 p99=0.2073 p99.9=0.2073 max=0.2073 mean=0.1411
- join: {'joined': 20, 'by_nonce_fallback': 0, 'provider_records': 20, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=20 p50=91.6724 p90=118.0572 p99=140.1775 p99.9=140.1775 max=140.1775 mean=96.5016 |
| T_addon_first | n=20 p50=748.0191 p90=884.5811 p99=1062.435 p99.9=1062.435 max=1062.435 mean=587.8902 |
| T_release_lag_max | n=3 p50=109.6306 p90=1120.8947 p99=1120.8947 p99.9=1120.8947 max=1120.8947 mean=437.019 |
| T_release_lag_max_arrival | n=3 p50=109.6306 p90=1120.8947 p99=1120.8947 p99.9=1120.8947 max=1120.8947 mean=437.019 |
| T_fw_addon | n=20 p50=748.0191 p90=884.5811 p99=1120.8947 p99.9=1120.8947 max=1120.8947 mean=595.9082 |
| T_fw_addon_sse | n=14 p50=764.607 p90=1062.435 p99=1120.8947 p99.9=1120.8947 max=1120.8947 mean=812.4642 |
| T_fw_addon_json | n=6 p50=80.5317 p90=118.0572 p99=118.0572 p99.9=118.0572 max=118.0572 mean=90.6108 |
| client_ttft_sse | n=14 p50=914.6773 p90=1110.5596 p99=1212.4798 p99.9=1212.4798 max=1212.4798 mean=951.0547 |
| provider_sched_err_last | n=20 p50=0.0287 p90=0.0901 p99=0.0982 p99.9=0.0982 max=0.0982 mean=0.0412 |
| provider_sched_err_max_per_stream | n=20 p50=0.051 p90=0.1085 p99=0.1244 p99.9=0.1244 max=0.1244 mean=0.0551 |
| provider_write_max | n=20 p50=0.0307 p90=0.0563 p99=0.0904 p99.9=0.0904 max=0.0904 mean=0.0376 |

- release lag: 3 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/v1-bench/runs/verify20b/lg-v1ready', 'samples': 9, 'busy_max': 8.109138516806059, 'busy_mean': 4.08, 'machine': 'c4-standard-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 24, 'busy_max': 8.581201559358764, 'busy_p95': 8.131600359590541}
- completeness: [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/v1-bench/runs/verify20b/lg-v1ready', 'scheduled': 20, 'recorded': 20, 'interrupted': False}]
- health olg verify20b: gc_cycles=0 sched_latency_max_ms=0.08192 tcp=None
- health synthprov rv-v1-prov-1: gc_cycles=0 sched_latency_max_ms=0.08192 tcp={'TcpRetransSegs': 1, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 1, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 2739, 'TcpInSegs': 2737}
