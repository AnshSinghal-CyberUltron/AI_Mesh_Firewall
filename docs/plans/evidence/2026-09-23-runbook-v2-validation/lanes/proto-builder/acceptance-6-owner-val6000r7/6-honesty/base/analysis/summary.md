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
- schedule drops (>5.0 ms): 0; lateness ms: n=240 p50=0.1149 p90=0.1333 p99=0.1646 p99.9=0.2156 max=0.2156 mean=0.1139
- join: {'joined': 238, 'by_nonce_fallback': 0, 'provider_records': 258, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=238 p50=86.3239 p90=93.6092 p99=137.8447 p99.9=152.6035 max=152.6035 mean=74.705 |
| T_addon_first | n=238 p50=86.2561 p90=94.1566 p99=137.818 p99.9=152.6035 max=152.6035 mean=74.9324 |
| T_release_lag_max | n=238 p50=103.5763 p90=130.0801 p99=152.6035 p99.9=177.6489 max=177.6489 mean=98.5773 |
| T_release_lag_max_arrival | n=238 p50=92.4682 p90=113.4535 p99=138.4235 p99.9=157.9575 max=157.9575 mean=89.1566 |
| T_fw_addon | n=238 p50=103.5763 p90=130.0801 p99=152.6035 p99.9=177.6489 max=177.6489 mean=98.5773 |
| T_fw_addon_sse | n=167 p50=109.8572 p90=131.9292 p99=163.8495 p99.9=177.6489 max=177.6489 mean=109.7763 |
| T_fw_addon_json | n=71 p50=85.6389 p90=91.7076 p99=152.6035 p99.9=152.6035 max=152.6035 mean=72.236 |
| client_ttft_sse | n=167 p50=236.5415 p90=244.6771 p99=261.7378 p99.9=287.8503 max=287.8503 mean=226.135 |
| provider_sched_err_last | n=238 p50=0.0554 p90=0.0939 p99=0.1161 p99.9=0.2657 max=0.2657 mean=0.0557 |
| provider_sched_err_max_per_stream | n=238 p50=0.0664 p90=0.1096 p99=1.6668 p99.9=2.9546 max=2.9546 mean=0.1081 |
| provider_write_max | n=238 p50=0.0526 p90=0.0757 p99=0.541 p99.9=0.7301 max=0.7301 mean=0.0612 |

- release lag: 238 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/proto-builder/acceptance-6-owner-val6000r7/6-honesty/base/olg', 'samples': 60, 'busy_max': 71.13563618637076, 'busy_mean': 18.39, 'machine': 'c4-standard-16', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 0, 'busy_max': None, 'busy_p95': None}
- completeness: [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/proto-builder/acceptance-6-owner-val6000r7/6-honesty/base/olg', 'scheduled': 260, 'recorded': 260, 'interrupted': False}]
- health olg base: gc_cycles=0 sched_latency_max_ms=0.16384 tcp=None
