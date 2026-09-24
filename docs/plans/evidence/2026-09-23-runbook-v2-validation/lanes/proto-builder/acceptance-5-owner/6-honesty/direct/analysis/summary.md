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
- schedule drops (>5.0 ms): 0; lateness ms: n=240 p50=0.1056 p90=0.122 p99=0.1467 p99.9=0.171 max=0.171 mean=0.102
- join: {'joined': 240, 'by_nonce_fallback': 0, 'provider_records': 260, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=240 p50=0.1401 p90=0.1997 p99=0.2362 p99.9=0.2857 max=0.2857 mean=0.1501 |
| T_addon_first | n=240 p50=0.1561 p90=0.1995 p99=0.2362 p99.9=0.29 max=0.29 mean=0.1615 |
| T_release_lag_max | n=240 p50=0.1788 p90=0.2567 p99=0.3963 p99.9=0.4172 max=0.4172 mean=0.1903 |
| T_release_lag_max_arrival | n=240 p50=0.1788 p90=0.2567 p99=0.3963 p99.9=0.4172 max=0.4172 mean=0.1903 |
| T_fw_addon | n=240 p50=0.1788 p90=0.2567 p99=0.3963 p99.9=0.4172 max=0.4172 mean=0.1903 |
| T_fw_addon_sse | n=168 p50=0.1713 p90=0.274 p99=0.4051 p99.9=0.4172 max=0.4172 mean=0.1913 |
| T_fw_addon_json | n=72 p50=0.1874 p90=0.2076 p99=0.2362 p99.9=0.2362 max=0.2362 mean=0.188 |
| client_ttft_sse | n=168 p50=150.1697 p90=150.243 p99=150.3902 p99.9=150.4001 max=150.4001 mean=150.1863 |
| provider_sched_err_last | n=240 p50=0.0278 p90=0.079 p99=0.1002 p99.9=0.1277 max=0.1277 mean=0.0362 |
| provider_sched_err_max_per_stream | n=240 p50=0.0747 p90=0.192 p99=0.5493 p99.9=0.5922 max=0.5922 mean=0.0905 |
| provider_write_max | n=240 p50=0.029 p90=0.0404 p99=0.0561 p99.9=0.0669 max=0.0669 mean=0.0281 |

- release lag: 240 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/proto-builder/acceptance-5-owner/6-honesty/direct/olg', 'samples': 60, 'busy_max': 22.055261028822905, 'busy_mean': 9.52, 'machine': 'c4-standard-16', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 0, 'busy_max': None, 'busy_p95': None}
- completeness: [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/proto-builder/acceptance-5-owner/6-honesty/direct/olg', 'scheduled': 260, 'recorded': 260, 'interrupted': False}]
- health olg direct: gc_cycles=0 sched_latency_max_ms=0.114688 tcp=None
