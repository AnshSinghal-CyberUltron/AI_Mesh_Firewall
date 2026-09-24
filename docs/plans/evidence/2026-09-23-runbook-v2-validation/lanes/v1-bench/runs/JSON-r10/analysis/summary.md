# Harness analysis (sut, policy=none) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 1800 over 180 s = 10.0 req/s (configured 10.0)
- qualified: 1800 (10.0 req/s); expected blocks: 0; errors: 0 (rate 0.0)
- error reasons: {}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=1800 p50=0.113 p90=0.169 p99=0.2481 p99.9=0.2896 max=0.2995 mean=0.1194
- join: {'joined': 1800, 'by_nonce_fallback': 0, 'provider_records': 2150, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=1800 p50=79.8386 p90=130.9255 p99=218.7158 p99.9=288.1424 max=314.4253 mean=87.016 |
| T_addon_first | n=1800 p50=79.8386 p90=130.9255 p99=218.7158 p99.9=288.1424 max=314.4253 mean=87.016 |
| T_release_lag_max | n=182 p50=83.1714 p90=138.1901 p99=194.484 p99.9=226.3086 max=226.3086 mean=89.0578 |
| T_release_lag_max_arrival | n=182 p50=83.1714 p90=138.1901 p99=194.484 p99.9=226.3086 max=226.3086 mean=89.0578 |
| T_fw_addon | n=1800 p50=79.8386 p90=130.9255 p99=218.7158 p99.9=288.1424 max=314.4253 mean=87.016 |
| T_fw_addon_sse | n=0 |
| T_fw_addon_json | n=1800 p50=79.8386 p90=130.9255 p99=218.7158 p99.9=288.1424 max=314.4253 mean=87.016 |
| client_ttft_sse | n=0 |
| provider_sched_err_last | n=1800 p50=0.0557 p90=0.0919 p99=0.1014 p99.9=0.1908 max=0.2511 mean=0.0555 |
| provider_sched_err_max_per_stream | n=1800 p50=0.0557 p90=0.0919 p99=0.1014 p99.9=0.1908 max=0.2511 mean=0.0555 |
| provider_write_max | n=1800 p50=0.0132 p90=0.0232 p99=0.0484 p99.9=0.0674 max=0.076 mean=0.0148 |

- release lag: 182 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/v1-bench/runs/JSON-r10/lg-v1aug', 'samples': 180, 'busy_max': 7.975306808202931, 'busy_mean': 2.68, 'machine': 'c4-standard-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 238, 'busy_max': 16.146763766126114, 'busy_p95': 4.257120002328985}
- completeness: [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/v1-bench/runs/JSON-r10/lg-v1aug', 'scheduled': 2150, 'recorded': 2150, 'interrupted': False}]
- health olg JSON-r10: gc_cycles=0 sched_latency_max_ms=0.114688 tcp=None
- health synthprov rv-v1-prov-2: gc_cycles=0 sched_latency_max_ms=0.196608 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 7750, 'TcpInSegs': 6414}
