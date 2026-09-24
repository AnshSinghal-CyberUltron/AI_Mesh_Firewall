# Harness analysis (sut, policy=none) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 6000 over 300 s = 20.0 req/s (configured 20.0)
- qualified: 6000 (20.0 req/s); expected blocks: 0; errors: 0 (rate 0.0)
- error reasons: {}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=6000 p50=0.0874 p90=0.0979 p99=0.111 p99.9=0.1339 max=0.3294 mean=0.0882
- join: {'joined': 6000, 'by_nonce_fallback': 0, 'provider_records': 7500, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=6000 p50=246.7233 p90=1361.9377 p99=3214.7035 p99.9=5325.0317 max=6128.3725 mean=544.8971 |
| T_addon_first | n=6000 p50=757.4457 p90=966.138 p99=1188.9151 p99.9=1358.1455 max=1427.3137 mean=622.5915 |
| T_release_lag_max | n=582 p50=1144.7784 p90=1771.9707 p99=3766.5624 p99.9=6082.1581 max=6082.1581 mean=1059.7053 |
| T_release_lag_max_arrival | n=582 p50=1144.7784 p90=1771.9707 p99=3766.5624 p99.9=6082.1581 max=6082.1581 mean=1059.7053 |
| T_fw_addon | n=6000 p50=788.5556 p90=1436.5944 p99=3240.1289 p99.9=5325.0317 max=6128.3725 mean=802.8758 |
| T_fw_addon_sse | n=4200 p50=879.4599 p90=1688.5788 p99=3506.143 p99.9=5424.0051 max=6128.3725 mean=1071.2833 |
| T_fw_addon_json | n=1800 p50=167.3512 p90=243.1178 p99=435.2433 p99.9=513.677 max=565.8744 mean=176.5917 |
| client_ttft_sse | n=4200 p50=965.7481 p90=1174.564 p99=1362.6608 p99.9=1520.8451 max=1577.3491 mean=963.7857 |
| provider_sched_err_last | n=6000 p50=0.0525 p90=0.0886 p99=0.1034 p99.9=0.1134 max=0.1196 mean=0.0517 |
| provider_sched_err_max_per_stream | n=6000 p50=0.0734 p90=0.1114 p99=0.13 p99.9=0.1697 max=0.3124 mean=0.0724 |
| provider_write_max | n=6000 p50=0.0124 p90=0.02 p99=0.0293 p99.9=0.0409 max=0.0905 mean=0.0137 |

- release lag: 582 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/v1-bench/runs/L1-r20/lg-v1aug', 'samples': 300, 'busy_max': 15.426921147722261, 'busy_mean': 4.55, 'machine': 'c4-standard-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 412, 'busy_max': 17.88981957870115, 'busy_p95': 5.049306332649028}
- completeness: [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/v1-bench/runs/L1-r20/lg-v1aug', 'scheduled': 7500, 'recorded': 7500, 'interrupted': False}]
- health olg L1-r20: gc_cycles=0 sched_latency_max_ms=0.16384 tcp=None
- health synthprov rv-v1-prov-1: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 3074, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 3074, 'TcpExtTCPTimeouts': 1, 'TcpOutSegs': 1205164, 'TcpInSegs': 1197605}
