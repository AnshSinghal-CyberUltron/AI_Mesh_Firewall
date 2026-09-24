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
- qualified: 239 (3.98 req/s); expected blocks: 0; errors: 1 (rate 0.004166666666666667)
- error reasons: {'http_403': 1, 'incomplete': 1, 'unjoined': 1, 'disposition_BLOCK': 1, 'stage_dispatch_S': 1, 'stage_out_S': 1}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=240 p50=0.1116 p90=0.128 p99=0.155 p99.9=0.2623 max=0.2623 mean=0.1096
- join: {'joined': 239, 'by_nonce_fallback': 0, 'provider_records': 259, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=239 p50=85.0409 p90=88.4905 p99=105.3169 p99.9=109.8435 max=109.8435 mean=71.8133 |
| T_addon_first | n=239 p50=85.0123 p90=88.6735 p99=107.782 p99.9=111.0477 max=111.0477 mean=72.4373 |
| T_release_lag_max | n=239 p50=96.7043 p90=138.198 p99=146.8138 p99.9=159.9986 max=159.9986 mean=106.9529 |
| T_release_lag_max_arrival | n=239 p50=96.0346 p90=138.0933 p99=145.5888 p99.9=159.9986 max=159.9986 mean=106.5801 |
| T_fw_addon | n=239 p50=96.7043 p90=138.198 p99=146.8138 p99.9=159.9986 max=159.9986 mean=106.9529 |
| T_fw_addon_sse | n=168 p50=135.1734 p90=138.4481 p99=148.6284 p99.9=159.9986 max=159.9986 mean=122.6726 |
| T_fw_addon_json | n=71 p50=83.9335 p90=88.8281 p99=106.1986 p99.9=106.1986 max=106.1986 mean=69.757 |
| client_ttft_sse | n=168 p50=235.3483 p90=238.5968 p99=259.8315 p99.9=261.0767 max=261.0767 mean=223.6221 |
| provider_sched_err_last | n=239 p50=0.0473 p90=0.0935 p99=0.1004 p99.9=0.1005 max=0.1005 mean=0.0511 |
| provider_sched_err_max_per_stream | n=239 p50=0.0604 p90=0.1033 p99=0.1805 p99.9=0.4052 max=0.4052 mean=0.064 |
| provider_write_max | n=239 p50=0.0492 p90=0.0641 p99=0.1907 p99.9=0.2048 max=0.2048 mean=0.0451 |

- release lag: 239 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/proto-builder/acceptance-3/6-honesty/hold30/olg', 'samples': 60, 'busy_max': 27.666140912107473, 'busy_mean': 13.69, 'machine': 'c4-standard-16', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 0, 'busy_max': None, 'busy_p95': None}
- completeness: [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/proto-builder/acceptance-3/6-honesty/hold30/olg', 'scheduled': 260, 'recorded': 260, 'interrupted': False}]
- health olg hold30: gc_cycles=0 sched_latency_max_ms=0.098304 tcp=None
