# Harness analysis (sut, policy={'blocks_total': 2, 'expected_blocks': 0, 'false_positive_blocks': 2, 'other_blocks': 0, 'benign_offered': 240, 'false_positive_rate': 0.008333333333333333, 'policy_misses': 0, 'latency_client_total_ms': {'policy_block_fp': {'n': 2, 'min': 41.7348, 'p50': 41.7348, 'p90': 42.7317, 'p99': 42.7317, 'p999': 42.7317, 'max': 42.7317, 'mean': 42.2332}}, 'block_rule': {'statuses': [400, 403, 422, 451], 'envelope_re': '(?i)content_filter|content_polic|polic(y|ies)_|blocked|block_|guard|violation|unsafe|threat'}}) — FAIL

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
- policy cohort latency policy_block_fp (client total ms): n=2 p50=41.7348 p90=42.7317 p99=42.7317 p99.9=42.7317 max=42.7317 mean=42.2332
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=240 p50=0.1163 p90=0.1392 p99=0.1699 p99.9=0.3163 max=0.3163 mean=0.1186
- join: {'joined': 238, 'by_nonce_fallback': 0, 'provider_records': 258, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=238 p50=91.4076 p90=96.5703 p99=128.285 p99.9=143.2952 max=143.2952 mean=78.8605 |
| T_addon_first | n=238 p50=91.4066 p90=96.9671 p99=128.2626 p99.9=143.1862 max=143.1862 mean=78.9866 |
| T_release_lag_max | n=238 p50=103.7051 p90=133.943 p99=163.2546 p99.9=183.2696 max=183.2696 mean=103.6656 |
| T_release_lag_max_arrival | n=238 p50=99.0619 p90=115.8557 p99=149.0579 p99.9=167.2303 max=167.2303 mean=93.3258 |
| T_fw_addon | n=238 p50=103.7051 p90=133.943 p99=163.2546 p99.9=183.2696 max=183.2696 mean=103.6656 |
| T_fw_addon_sse | n=167 p50=115.8075 p90=135.6295 p99=168.0988 p99.9=183.2696 max=183.2696 mean=115.1603 |
| T_fw_addon_json | n=71 p50=91.2328 p90=97.754 p99=114.7431 p99.9=114.7431 max=114.7431 mean=76.6288 |
| client_ttft_sse | n=167 p50=241.4619 p90=246.4239 p99=293.1443 p99.9=293.2114 max=293.2114 mean=230.0434 |
| provider_sched_err_last | n=238 p50=0.0522 p90=0.0953 p99=0.1028 p99.9=0.1056 max=0.1056 mean=0.0555 |
| provider_sched_err_max_per_stream | n=238 p50=0.0615 p90=0.1026 p99=0.1322 p99.9=0.9877 max=0.9877 mean=0.0705 |
| provider_write_max | n=238 p50=0.0518 p90=0.0752 p99=0.2133 p99.9=0.2748 max=0.2748 mean=0.05 |

- release lag: 238 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/proto-builder/acceptance-7-frozen1-harnessREADY/owner/6-honesty/predispatch5/olg', 'samples': 60, 'busy_max': 40.978643740195345, 'busy_mean': 21.41, 'machine': 'c4-standard-16', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 0, 'busy_max': None, 'busy_p95': None}
- completeness: [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/proto-builder/acceptance-7-frozen1-harnessREADY/owner/6-honesty/predispatch5/olg', 'scheduled': 260, 'recorded': 260, 'interrupted': False}]
- health olg predispatch5: gc_cycles=0 sched_latency_max_ms=0.08192 tcp=None
