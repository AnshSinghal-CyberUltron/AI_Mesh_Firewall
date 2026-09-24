# Harness analysis (sut, policy=enforce) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 240 over 60 s = 4.0 req/s (configured 4.0)
- qualified: 240 (4.0 req/s); expected blocks: 0; errors: 0 (rate 0.0)
- error reasons: {}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=240 p50=0.1149 p90=0.1326 p99=0.1538 p99.9=0.1711 max=0.1711 mean=0.1146
- join: {'joined': 240, 'by_nonce_fallback': 0, 'provider_records': 260, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=240 p50=84.416 p90=88.5796 p99=99.8219 p99.9=103.8241 max=103.8241 mean=71.6466 |
| T_addon_first | n=240 p50=84.4168 p90=88.8397 p99=103.8241 p99.9=107.2724 max=107.2724 mean=71.9554 |
| T_release_lag_max | n=240 p50=101.7936 p90=138.8774 p99=156.0153 p99.9=156.1828 max=156.1828 mean=107.0336 |
| T_release_lag_max_arrival | n=240 p50=100.0254 p90=137.2917 p99=146.1665 p99.9=149.0688 max=149.0688 mean=106.3274 |
| T_fw_addon | n=240 p50=101.7936 p90=138.8774 p99=156.0153 p99.9=156.1828 max=156.1828 mean=107.0336 |
| T_fw_addon_sse | n=168 p50=134.425 p90=140.8916 p99=156.0279 p99.9=156.1828 max=156.1828 mean=123.0379 |
| T_fw_addon_json | n=72 p50=83.5828 p90=89.2805 p99=103.8241 p99.9=103.8241 max=103.8241 mean=69.6904 |
| client_ttft_sse | n=168 p50=234.6522 p90=238.8974 p99=255.6492 p99.9=257.3296 max=257.3296 mean=222.9817 |
| provider_sched_err_last | n=240 p50=0.0561 p90=0.0945 p99=0.1046 p99.9=0.1215 max=0.1215 mean=0.0551 |
| provider_sched_err_max_per_stream | n=240 p50=0.0694 p90=0.1039 p99=0.2092 p99.9=0.2703 max=0.2703 mean=0.0685 |
| provider_write_max | n=240 p50=0.0471 p90=0.066 p99=0.2 p99.9=0.2245 max=0.2245 mean=0.0464 |

- release lag: 240 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/proto-builder/acceptance-1/6-honesty/hold30/olg', 'samples': 60, 'busy_max': 26.990247329580598, 'busy_mean': 13.22, 'machine': 'c4-standard-16', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 0, 'busy_max': None, 'busy_p95': None}
- completeness: [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/proto-builder/acceptance-1/6-honesty/hold30/olg', 'scheduled': 260, 'recorded': 260, 'interrupted': False}]
- health olg hold30: gc_cycles=0 sched_latency_max_ms=0.08192 tcp=None
