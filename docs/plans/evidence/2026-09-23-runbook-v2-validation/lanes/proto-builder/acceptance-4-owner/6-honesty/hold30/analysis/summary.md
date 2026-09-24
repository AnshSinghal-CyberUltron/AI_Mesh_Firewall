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
- schedule drops (>5.0 ms): 0; lateness ms: n=240 p50=0.1114 p90=0.1274 p99=0.1502 p99.9=0.4142 max=0.4142 mean=0.1126
- join: {'joined': 238, 'by_nonce_fallback': 0, 'provider_records': 258, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=238 p50=85.7573 p90=88.8997 p99=104.3639 p99.9=114.9063 max=114.9063 mean=72.5771 |
| T_addon_first | n=238 p50=85.7484 p90=89.1517 p99=104.3588 p99.9=114.9271 max=114.9271 mean=72.6566 |
| T_release_lag_max | n=238 p50=95.9957 p90=138.2805 p99=157.6016 p99.9=166.2216 max=166.2216 mean=107.5903 |
| T_release_lag_max_arrival | n=238 p50=95.9957 p90=138.3009 p99=154.0868 p99.9=166.2216 max=166.2216 mean=107.2128 |
| T_fw_addon | n=238 p50=95.9957 p90=138.2805 p99=157.6016 p99.9=166.2216 max=166.2216 mean=107.5903 |
| T_fw_addon_sse | n=167 p50=135.7604 p90=139.2448 p99=158.615 p99.9=166.2216 max=166.2216 mean=123.6411 |
| T_fw_addon_json | n=71 p50=85.2148 p90=88.3796 p99=100.2665 p99.9=100.2665 max=100.2665 mean=69.8371 |
| client_ttft_sse | n=167 p50=235.9673 p90=239.4349 p99=258.8156 p99.9=265.0101 max=265.0101 mean=223.9083 |
| provider_sched_err_last | n=238 p50=0.0514 p90=0.0902 p99=0.1017 p99.9=0.1074 max=0.1074 mean=0.0522 |
| provider_sched_err_max_per_stream | n=238 p50=0.062 p90=0.1017 p99=0.137 p99.9=0.5394 max=0.5394 mean=0.065 |
| provider_write_max | n=238 p50=0.0516 p90=0.0671 p99=0.1218 p99.9=0.2626 max=0.2626 mean=0.0463 |

- release lag: 238 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/proto-builder/acceptance-4-owner/6-honesty/hold30/olg', 'samples': 60, 'busy_max': 26.873596976724723, 'busy_mean': 14.36, 'machine': 'c4-standard-16', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 0, 'busy_max': None, 'busy_p95': None}
- completeness: [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/proto-builder/acceptance-4-owner/6-honesty/hold30/olg', 'scheduled': 260, 'recorded': 260, 'interrupted': False}]
- health olg hold30: gc_cycles=0 sched_latency_max_ms=0.229376 tcp=None
