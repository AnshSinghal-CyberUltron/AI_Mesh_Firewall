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
- schedule drops (>5.0 ms): 0; lateness ms: n=240 p50=0.0545 p90=0.0657 p99=0.0887 p99.9=0.2572 max=0.2572 mean=0.0571
- join: {'joined': 238, 'by_nonce_fallback': 0, 'provider_records': 258, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=238 p50=84.8336 p90=88.2445 p99=100.0228 p99.9=106.4256 max=106.4256 mean=71.548 |
| T_addon_first | n=238 p50=84.7757 p90=88.3605 p99=105.2963 p99.9=106.4387 max=106.4387 mean=71.8471 |
| T_release_lag_max | n=238 p50=88.0721 p90=126.8079 p99=145.0866 p99.9=149.5842 max=149.5842 mean=95.1134 |
| T_release_lag_max_arrival | n=238 p50=88.0721 p90=108.4334 p99=120.0204 p99.9=126.4274 max=126.4274 mean=85.8597 |
| T_fw_addon | n=238 p50=88.0721 p90=126.8079 p99=145.0866 p99.9=149.5842 max=149.5842 mean=95.1134 |
| T_fw_addon_sse | n=167 p50=107.6793 p90=127.2268 p99=147.5734 p99.9=149.5842 max=149.5842 mean=106.2405 |
| T_fw_addon_json | n=71 p50=84.3331 p90=88.0193 p99=102.2527 p99.9=102.2527 max=102.2527 mean=68.9413 |
| client_ttft_sse | n=167 p50=234.8918 p90=239.1672 p99=256.4473 p99.9=256.4575 max=256.4575 mean=223.1332 |
| provider_sched_err_last | n=238 p50=0.0474 p90=0.0931 p99=0.1016 p99.9=0.1039 max=0.1039 mean=0.0513 |
| provider_sched_err_max_per_stream | n=238 p50=0.0587 p90=0.1011 p99=0.2342 p99.9=0.2955 max=0.2955 mean=0.0621 |
| provider_write_max | n=238 p50=0.0363 p90=0.0579 p99=0.0902 p99.9=0.1907 max=0.1907 mean=0.0412 |

- release lag: 238 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/proto-builder/honesty/base/olg', 'samples': 60, 'busy_max': 19.52165481577246, 'busy_mean': 8.48, 'machine': 'c4-standard-16', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 0, 'busy_max': None, 'busy_p95': None}
- completeness: [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/proto-builder/honesty/base/olg', 'scheduled': 260, 'recorded': 260, 'interrupted': False}]
