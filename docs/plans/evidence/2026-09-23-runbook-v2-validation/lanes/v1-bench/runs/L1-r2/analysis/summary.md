# Harness analysis (sut, policy=none) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 600 over 300 s = 2.0 req/s (configured 2.0)
- qualified: 600 (2.0 req/s); expected blocks: 0; errors: 0 (rate 0.0)
- error reasons: {}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=600 p50=0.1168 p90=0.162 p99=0.2305 p99.9=0.3116 max=0.3116 mean=0.1244
- join: {'joined': 600, 'by_nonce_fallback': 0, 'provider_records': 750, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=600 p50=89.7375 p90=122.2216 p99=273.6495 p99.9=401.5621 max=401.5621 mean=95.5767 |
| T_addon_first | n=600 p50=710.2582 p90=827.101 p99=1039.3966 p99.9=1104.5155 max=1104.5155 mean=535.3752 |
| T_release_lag_max | n=52 p50=1046.6649 p90=1122.9903 p99=1183.5043 p99.9=1183.5043 max=1183.5043 mean=732.4474 |
| T_release_lag_max_arrival | n=52 p50=1046.6649 p90=1122.9903 p99=1183.5043 p99.9=1183.5043 max=1183.5043 mean=732.4474 |
| T_fw_addon | n=600 p50=716.8054 p90=905.5702 p99=1113.1567 p99.9=1183.5043 max=1183.5043 mean=554.6362 |
| T_fw_addon_sse | n=420 p50=747.4996 p90=1018.2718 p99=1139.1391 p99.9=1183.5043 max=1183.5043 mean=757.8384 |
| T_fw_addon_json | n=180 p50=79.919 p90=108.0232 p99=143.9999 p99.9=234.3742 max=234.3742 mean=80.4978 |
| client_ttft_sse | n=420 p50=891.2921 p90=1022.9106 p99=1212.3374 p99.9=1254.5786 max=1254.5786 mean=880.3766 |
| provider_sched_err_last | n=600 p50=0.053 p90=0.0922 p99=0.1034 p99.9=0.236 max=0.236 mean=0.0529 |
| provider_sched_err_max_per_stream | n=600 p50=0.0663 p90=0.1092 p99=0.2526 p99.9=0.2809 max=0.2809 mean=0.0708 |
| provider_write_max | n=600 p50=0.0172 p90=0.0264 p99=0.0408 p99.9=0.0914 max=0.0914 mean=0.0184 |

- release lag: 52 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/v1-bench/runs/L1-r2/lg-v1aug', 'samples': 300, 'busy_max': 17.26037622256721, 'busy_mean': 3.73, 'machine': 'c4-standard-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 412, 'busy_max': 31.5566715975784, 'busy_p95': 9.224648003360691}
- completeness: [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/v1-bench/runs/L1-r2/lg-v1aug', 'scheduled': 750, 'recorded': 750, 'interrupted': False}]
- health olg L1-r2: gc_cycles=0 sched_latency_max_ms=0.098304 tcp=None
- health synthprov rv-v1-prov-1: gc_cycles=0 sched_latency_max_ms=0.196608 tcp={'TcpRetransSegs': 75, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 75, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 121370, 'TcpInSegs': 120952}
