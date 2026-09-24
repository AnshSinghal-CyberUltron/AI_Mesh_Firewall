# Harness analysis (sut, policy={'blocks_total': 2, 'expected_blocks': 0, 'false_positive_blocks': 2, 'other_blocks': 0, 'benign_offered': 240, 'false_positive_rate': 0.008333333333333333, 'policy_misses': 0, 'latency_client_total_ms': {'policy_block_fp': {'n': 2, 'min': 45.2029, 'p50': 45.2029, 'p90': 64.277, 'p99': 64.277, 'p999': 64.277, 'max': 64.277, 'mean': 54.74}}, 'block_rule': {'statuses': [400, 403, 422, 451], 'envelope_re': '(?i)content_filter|content_polic|polic(y|ies)_|blocked|block_|guard|violation|unsafe|threat'}}) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| infra_error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 240 over 60 s = 4.0 req/s (configured 4.0)
- qualified: 238 (3.97 req/s; dispositions ['ALLOW', 'FLAG', 'REDACT'], source auto)
- infra errors (the error-budget gate): 0 (rate 0.0); reasons: {}
- policy stratum (not qualified, not infra errors): expected blocks 0, FALSE-POSITIVE blocks 2 of 240 benign (FP rate 0.008333333333333333), other blocks 0, policy misses 0
- policy cohort latency policy_block_fp (client total ms): n=2 p50=45.2029 p90=64.277 p99=64.277 p99.9=64.277 max=64.277 mean=54.74
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=240 p50=0.1184 p90=0.1415 p99=0.1906 p99.9=0.2463 max=0.2463 mean=0.119
- join: {'joined': 238, 'by_nonce_fallback': 0, 'provider_records': 258, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=238 p50=86.6605 p90=97.7379 p99=136.654 p99.9=137.5919 max=137.5919 mean=75.9388 |
| T_addon_first | n=238 p50=86.623 p90=98.6991 p99=136.7003 p99.9=137.5703 max=137.5703 mean=76.1579 |
| T_release_lag_max | n=238 p50=104.7977 p90=134.9338 p99=156.9332 p99.9=163.7038 max=163.7038 mean=100.7162 |
| T_release_lag_max_arrival | n=238 p50=100.9243 p90=115.3107 p99=143.7877 p99.9=161.1374 max=161.1374 mean=90.4169 |
| T_fw_addon | n=238 p50=104.7977 p90=134.9338 p99=156.9332 p99.9=163.7038 max=163.7038 mean=100.7162 |
| T_fw_addon_sse | n=167 p50=123.4749 p90=138.5151 p99=161.1374 p99.9=163.7038 max=163.7038 mean=111.9787 |
| T_fw_addon_json | n=71 p50=86.4255 p90=99.5722 p99=137.449 p99.9=137.449 max=137.449 mean=74.2256 |
| client_ttft_sse | n=167 p50=236.7846 p90=247.7289 p99=286.7205 p99.9=287.6267 max=287.6267 mean=227.034 |
| provider_sched_err_last | n=238 p50=0.051 p90=0.0889 p99=0.1013 p99.9=0.1021 max=0.1021 mean=0.053 |
| provider_sched_err_max_per_stream | n=238 p50=0.0631 p90=0.1051 p99=0.148 p99.9=0.2217 max=0.2217 mean=0.0657 |
| provider_write_max | n=238 p50=0.0527 p90=0.0755 p99=0.2083 p99.9=0.3801 max=0.3801 mean=0.0514 |

- release lag: 238 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/proto-builder/acceptance-7-frozen1-harnessREADY/owner/6-honesty/base/olg', 'samples': 60, 'busy_max': 45.024777664851555, 'busy_mean': 24.72, 'machine': 'c4-standard-16', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 0, 'busy_max': None, 'busy_p95': None}
- completeness: [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/proto-builder/acceptance-7-frozen1-harnessREADY/owner/6-honesty/base/olg', 'scheduled': 260, 'recorded': 260, 'interrupted': False}]
- health olg base: gc_cycles=0 sched_latency_max_ms=0.16384 tcp=None
