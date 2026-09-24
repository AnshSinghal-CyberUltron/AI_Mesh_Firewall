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
- schedule drops (>5.0 ms): 0; lateness ms: n=240 p50=0.1085 p90=0.1242 p99=0.1889 p99.9=0.2124 max=0.2124 mean=0.1096
- join: {'joined': 240, 'by_nonce_fallback': 0, 'provider_records': 260, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=240 p50=0.1426 p90=0.1975 p99=0.2848 p99.9=0.3108 max=0.3108 mean=0.1537 |
| T_addon_first | n=240 p50=0.1573 p90=0.198 p99=0.299 p99.9=0.32 max=0.32 mean=0.1641 |
| T_release_lag_max | n=240 p50=0.1761 p90=0.2106 p99=0.32 p99.9=0.3596 max=0.3596 mean=0.1826 |
| T_release_lag_max_arrival | n=240 p50=0.1761 p90=0.2106 p99=0.32 p99.9=0.3596 max=0.3596 mean=0.1826 |
| T_fw_addon | n=240 p50=0.1761 p90=0.2106 p99=0.32 p99.9=0.3596 max=0.3596 mean=0.1826 |
| T_fw_addon_sse | n=168 p50=0.1703 p90=0.2116 p99=0.3213 p99.9=0.3596 max=0.3596 mean=0.179 |
| T_fw_addon_json | n=72 p50=0.1887 p90=0.2106 p99=0.3108 p99.9=0.3108 max=0.3108 mean=0.191 |
| client_ttft_sse | n=168 p50=150.2207 p90=150.2505 p99=150.3312 p99.9=150.3644 max=150.3644 mean=150.2197 |
| provider_sched_err_last | n=240 p50=0.0691 p90=0.0946 p99=0.1192 p99.9=0.2547 max=0.2547 mean=0.0676 |
| provider_sched_err_max_per_stream | n=240 p50=0.0914 p90=0.2387 p99=0.3132 p99.9=0.7529 max=0.7529 mean=0.1248 |
| provider_write_max | n=240 p50=0.0286 p90=0.0378 p99=0.0803 p99.9=0.1204 max=0.1204 mean=0.0281 |

- release lag: 240 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/proto-builder/acceptance-4-owner/6-honesty/direct/olg', 'samples': 60, 'busy_max': 18.66884724116299, 'busy_mean': 8.66, 'machine': 'c4-standard-16', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 0, 'busy_max': None, 'busy_p95': None}
- completeness: [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/proto-builder/acceptance-4-owner/6-honesty/direct/olg', 'scheduled': 260, 'recorded': 260, 'interrupted': False}]
- health olg direct: gc_cycles=0 sched_latency_max_ms=0.114688 tcp=None
