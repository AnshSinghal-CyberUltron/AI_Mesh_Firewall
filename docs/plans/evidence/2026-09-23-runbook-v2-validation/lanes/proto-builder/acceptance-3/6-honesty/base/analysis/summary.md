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
- schedule drops (>5.0 ms): 0; lateness ms: n=240 p50=0.1119 p90=0.1278 p99=0.1736 p99.9=0.2591 max=0.2591 mean=0.1141
- join: {'joined': 238, 'by_nonce_fallback': 0, 'provider_records': 258, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=238 p50=85.6142 p90=89.5687 p99=99.2474 p99.9=108.9916 max=108.9916 mean=72.3942 |
| T_addon_first | n=238 p50=85.5701 p90=89.6162 p99=106.8468 p99.9=108.9916 max=108.9916 mean=72.6798 |
| T_release_lag_max | n=238 p50=89.8769 p90=127.9479 p99=132.3416 p99.9=145.6701 max=145.6701 mean=96.2536 |
| T_release_lag_max_arrival | n=238 p50=89.4747 p90=109.521 p99=117.6027 p99.9=125.9986 max=125.9986 mean=86.6921 |
| T_fw_addon | n=238 p50=89.8769 p90=127.9479 p99=132.3416 p99.9=145.6701 max=145.6701 mean=96.2536 |
| T_fw_addon_sse | n=167 p50=109.9335 p90=128.8154 p99=137.4723 p99.9=145.6701 max=145.6701 mean=107.3344 |
| T_fw_addon_json | n=71 p50=84.9572 p90=89.4269 p99=108.9916 p99.9=108.9916 max=108.9916 mean=70.1904 |
| client_ttft_sse | n=167 p50=235.9134 p90=239.7529 p99=256.8807 p99.9=257.6488 max=257.6488 mean=223.7947 |
| provider_sched_err_last | n=238 p50=0.0539 p90=0.0913 p99=0.1002 p99.9=0.1043 max=0.1043 mean=0.0541 |
| provider_sched_err_max_per_stream | n=238 p50=0.069 p90=0.1065 p99=0.3386 p99.9=0.4902 max=0.4902 mean=0.0746 |
| provider_write_max | n=238 p50=0.0489 p90=0.0641 p99=0.2077 p99.9=0.2677 max=0.2677 mean=0.0461 |

- release lag: 238 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/proto-builder/acceptance-3/6-honesty/base/olg', 'samples': 60, 'busy_max': 24.552094748404752, 'busy_mean': 14.39, 'machine': 'c4-standard-16', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 0, 'busy_max': None, 'busy_p95': None}
- completeness: [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/proto-builder/acceptance-3/6-honesty/base/olg', 'scheduled': 260, 'recorded': 260, 'interrupted': False}]
- health olg base: gc_cycles=0 sched_latency_max_ms=0.114688 tcp=None
