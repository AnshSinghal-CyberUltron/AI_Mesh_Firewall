# Harness analysis (sut, policy=enforce) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| error_rate_le_budget | FAIL |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 240 over 60 s = 4.0 req/s (configured 4.0)
- qualified: 238 (3.97 req/s); expected blocks: 0; errors: 2 (rate 0.008333333333333333)
- error reasons: {'http_403': 2, 'incomplete': 2, 'unjoined': 2, 'disposition_BLOCK': 2, 'stage_dispatch_S': 2, 'stage_out_S': 2}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=240 p50=0.1136 p90=0.1323 p99=0.2001 p99.9=0.2375 max=0.2375 mean=0.1149
- join: {'joined': 238, 'by_nonce_fallback': 0, 'provider_records': 258, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=238 p50=91.0253 p90=94.9334 p99=110.7495 p99.9=141.5698 max=141.5698 mean=78.213 |
| T_addon_first | n=238 p50=91.0253 p90=95.098 p99=111.0809 p99.9=141.6535 max=141.6535 mean=78.4287 |
| T_release_lag_max | n=238 p50=96.2727 p90=134.1244 p99=157.3291 p99.9=181.3429 max=181.3429 mean=102.7498 |
| T_release_lag_max_arrival | n=238 p50=94.2592 p90=115.5204 p99=131.4422 p99.9=162.312 max=162.312 mean=92.6615 |
| T_fw_addon | n=238 p50=96.2727 p90=134.1244 p99=157.3291 p99.9=181.3429 max=181.3429 mean=102.7498 |
| T_fw_addon_sse | n=167 p50=116.7162 p90=135.4798 p99=161.5692 p99.9=181.3429 max=181.3429 mean=114.4108 |
| T_fw_addon_json | n=71 p50=90.9972 p90=93.8437 p99=110.0779 p99.9=110.0779 max=110.0779 mean=75.3221 |
| client_ttft_sse | n=167 p50=241.1778 p90=245.9129 p99=290.918 p99.9=291.7084 max=291.7084 mean=229.8024 |
| provider_sched_err_last | n=238 p50=0.0514 p90=0.0934 p99=0.1022 p99.9=0.1183 max=0.1183 mean=0.0532 |
| provider_sched_err_max_per_stream | n=238 p50=0.0647 p90=0.1042 p99=0.37 p99.9=0.4033 max=0.4033 mean=0.0694 |
| provider_write_max | n=238 p50=0.0506 p90=0.0706 p99=0.2303 p99.9=0.4579 max=0.4579 mean=0.0504 |

- release lag: 238 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/proto-builder/acceptance-6-owner-val6000r7/6-honesty/predispatch5/olg', 'samples': 60, 'busy_max': 36.04271234028019, 'busy_mean': 21.14, 'machine': 'c4-standard-16', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 0, 'busy_max': None, 'busy_p95': None}
- completeness: [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/proto-builder/acceptance-6-owner-val6000r7/6-honesty/predispatch5/olg', 'scheduled': 260, 'recorded': 260, 'interrupted': False}]
- health olg predispatch5: gc_cycles=0 sched_latency_max_ms=0.08192 tcp=None
