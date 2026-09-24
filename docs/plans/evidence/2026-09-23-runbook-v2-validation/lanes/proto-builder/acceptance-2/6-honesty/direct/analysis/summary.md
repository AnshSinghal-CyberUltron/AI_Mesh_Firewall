# Harness analysis (direct, policy=enforce) — PASS

| check | result |
|---|---|
| zero_schedule_drops | PASS |
| zero_errors | PASS |
| provider_p99_sched_err_within_tol | PASS |
| loadgen_cpu_max_lt_limit | PASS |
| run_valid | PASS |
| all_joined | PASS |

- offered: 240 over 60 s = 4.0 req/s (configured 4.0)
- qualified: 240 (4.0 req/s); expected blocks: 0; errors: 0 (rate 0.0)
- error reasons: {}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=240 p50=0.107 p90=0.127 p99=0.168 p99.9=0.2427 max=0.2427 mean=0.1082
- join: {'joined': 240, 'by_nonce_fallback': 0, 'provider_records': 260, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=240 p50=0.1393 p90=0.1934 p99=0.2382 p99.9=0.3162 max=0.3162 mean=0.1482 |
| T_addon_first | n=240 p50=0.1559 p90=0.1954 p99=0.2382 p99.9=0.3162 max=0.3162 mean=0.1592 |
| T_release_lag_max | n=240 p50=0.1724 p90=0.2184 p99=0.3901 p99.9=0.4708 max=0.4708 mean=0.184 |
| T_release_lag_max_arrival | n=240 p50=0.1724 p90=0.2184 p99=0.3901 p99.9=0.4708 max=0.4708 mean=0.184 |
| T_fw_addon | n=240 p50=0.1724 p90=0.2184 p99=0.3901 p99.9=0.4708 max=0.4708 mean=0.184 |
| T_fw_addon_sse | n=168 p50=0.1709 p90=0.2237 p99=0.4325 p99.9=0.4708 max=0.4708 mean=0.1836 |
| T_fw_addon_json | n=72 p50=0.1799 p90=0.2155 p99=0.3162 p99.9=0.3162 max=0.3162 mean=0.185 |
| client_ttft_sse | n=168 p50=150.1941 p90=150.2352 p99=150.3455 p99.9=150.378 max=150.378 mean=150.2004 |
| provider_sched_err_last | n=240 p50=0.0525 p90=0.083 p99=0.1887 p99.9=0.2502 max=0.2502 mean=0.0543 |
| provider_sched_err_max_per_stream | n=240 p50=0.1 p90=0.2464 p99=0.3312 p99.9=0.3563 max=0.3563 mean=0.1328 |
| provider_write_max | n=240 p50=0.0302 p90=0.0398 p99=0.0721 p99.9=0.075 max=0.075 mean=0.0284 |

- release lag: 240 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/proto-builder/acceptance-2/6-honesty/direct/olg', 'samples': 60, 'busy_max': 17.688964427383922, 'busy_mean': 7.54, 'machine': 'c4-standard-16', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 0, 'busy_max': None, 'busy_p95': None}
- completeness: [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/proto-builder/acceptance-2/6-honesty/direct/olg', 'scheduled': 260, 'recorded': 260, 'interrupted': False}]
- health olg direct: gc_cycles=0 sched_latency_max_ms=0.049152 tcp=None
