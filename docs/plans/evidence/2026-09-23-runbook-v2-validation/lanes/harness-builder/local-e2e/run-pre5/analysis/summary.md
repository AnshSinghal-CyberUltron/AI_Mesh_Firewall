# Harness analysis (sut, policy=none) — PASS

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | PASS |
| error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 160 over 8 s = 20.0 req/s (configured 20.0)
- qualified: 160 (20.0 req/s); expected blocks: 0; errors: 0 (rate 0.0)
- error reasons: {}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=160 p50=0.0524 p90=0.0767 p99=0.5807 p99.9=3.669 max=3.669 mean=0.0826
- join: {'joined': 160, 'by_nonce_fallback': 0, 'provider_records': 200, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=160 p50=5.4248 p90=5.6288 p99=6.2077 p99.9=9.142 max=9.142 mean=5.4821 |
| T_addon_first | n=160 p50=5.4523 p90=5.6929 p99=6.2925 p99.9=6.4207 max=6.4207 mean=5.5065 |
| T_release_lag_max | n=160 p50=5.6241 p90=7.5598 p99=9.3087 p99.9=9.9123 max=9.9123 mean=6.1373 |
| T_release_lag_max_arrival | n=160 p50=5.6241 p90=7.5598 p99=9.3087 p99.9=9.9123 max=9.9123 mean=6.1373 |
| T_fw_addon | n=160 p50=5.6241 p90=7.5598 p99=9.3087 p99.9=9.9123 max=9.9123 mean=6.1375 |
| T_fw_addon_sse | n=112 p50=5.7255 p90=7.9768 p99=9.3087 p99.9=9.9123 max=9.9123 mean=6.3962 |
| T_fw_addon_json | n=48 p50=5.4925 p90=5.6786 p99=5.9525 p99.9=5.9525 max=5.9525 mean=5.5339 |
| client_ttft_sse | n=112 p50=155.4691 p90=155.7834 p99=156.5007 p99.9=156.702 max=156.702 mean=155.551 |
| provider_sched_err_last | n=160 p50=0.0468 p90=0.0868 p99=0.2008 p99.9=0.3907 max=0.3907 mean=0.0495 |
| provider_sched_err_max_per_stream | n=160 p50=0.1841 p90=5.1988 p99=5.4227 p99.9=5.4286 max=5.4286 mean=1.2014 |
| provider_write_max | n=160 p50=0.0381 p90=0.073 p99=0.1497 p99.9=0.2079 max=0.2079 mean=0.0469 |

- release lag: 160 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/harness-builder/local-e2e/run-pre5', 'samples': 8, 'busy_max': 33.983105912930476, 'busy_mean': 10.23, 'machine': 'c4-standard-16', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 0, 'busy_max': None, 'busy_p95': None}
- completeness: [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/harness-builder/local-e2e/run-pre5', 'scheduled': 200, 'recorded': 200, 'interrupted': False}]
