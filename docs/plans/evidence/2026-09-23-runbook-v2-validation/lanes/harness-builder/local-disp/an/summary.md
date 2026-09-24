# Harness analysis (direct, policy=none) — PASS

| check | result |
|---|---|
| zero_schedule_drops | PASS |
| zero_errors | PASS |
| provider_p99_sched_err_within_tol | PASS |
| loadgen_cpu_max_lt_limit | PASS |
| run_valid | PASS |
| all_joined | PASS |

- offered: 1020 over 5 s = 204.0 req/s (configured 200.0)
- qualified: 1020 (204.0 req/s); expected blocks: 0; errors: 0 (rate 0.0)
- error reasons: {}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=1020 p50=0.0886 p90=0.1027 p99=0.117 p99.9=0.139 max=0.1671 mean=0.0875
- join: {'joined': 1020, 'by_nonce_fallback': 0, 'provider_records': 1202, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=1020 p50=0.1102 p90=0.2385 p99=0.2974 p99.9=7.5994 max=9.8957 mean=0.166 |
| T_addon_first | n=1020 p50=0.1179 p90=0.2571 p99=0.3105 p99.9=7.5994 max=9.9182 mean=0.1782 |
| T_release_lag_max | n=86 p50=0.1342 p90=0.2722 p99=9.919 p99.9=9.919 max=9.919 mean=0.3685 |
| T_release_lag_max_arrival | n=86 p50=0.1342 p90=0.2722 p99=9.919 p99.9=9.919 max=9.919 mean=0.3685 |
| T_fw_addon | n=1020 p50=0.1192 p90=0.2584 p99=0.316 p99.9=7.5994 max=9.919 mean=0.1799 |
| T_fw_addon_sse | n=714 p50=0.1129 p90=0.2534 p99=0.316 p99.9=9.919 max=9.919 mean=0.1765 |
| T_fw_addon_json | n=306 p50=0.132 p90=0.2653 p99=0.3097 p99.9=7.5994 max=7.5994 mean=0.1878 |
| client_ttft_sse | n=714 p50=150.1682 p90=150.3082 p99=150.3788 p99.9=159.9678 max=159.9678 mean=150.2208 |
| provider_sched_err_last | n=1020 p50=0.0472 p90=0.0852 p99=0.1049 p99.9=0.1124 max=0.15 mean=0.0477 |
| provider_sched_err_max_per_stream | n=1020 p50=0.0732 p90=0.1194 p99=0.6733 p99.9=0.8154 max=0.8193 mean=0.0875 |
| provider_write_max | n=1020 p50=0.0195 p90=0.0437 p99=0.0554 p99.9=0.0707 max=0.0809 mean=0.0247 |

- release lag: 86 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/harness-builder/local-disp/lg', 'samples': 5, 'busy_max': 11.877591575417568, 'busy_mean': 9.16, 'machine': 'c4-standard-16', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 0, 'busy_max': None, 'busy_p95': None}
- completeness: [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/harness-builder/local-disp/lg', 'scheduled': 1202, 'recorded': 1202, 'interrupted': False}]
- health olg local-disp: gc_cycles=0 sched_latency_max_ms=0.16384 tcp=None
