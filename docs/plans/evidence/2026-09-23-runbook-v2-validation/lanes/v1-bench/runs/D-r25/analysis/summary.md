# Harness analysis (direct, policy=none) — PASS

| check | result |
|---|---|
| zero_schedule_drops | PASS |
| zero_errors | PASS |
| provider_p99_sched_err_within_tol | PASS |
| loadgen_cpu_max_lt_limit | PASS |
| run_valid | PASS |
| all_joined | PASS |

- offered: 7500 over 300 s = 25.0 req/s (configured 25.0)
- qualified: 7500 (25.0 req/s); expected blocks: 0; errors: 0 (rate 0.0)
- error reasons: {}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=7500 p50=0.0925 p90=0.1162 p99=0.2159 p99.9=0.261 max=0.2814 mean=0.0961
- join: {'joined': 7500, 'by_nonce_fallback': 0, 'provider_records': 9375, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=7500 p50=0.1904 p90=0.3366 p99=0.6288 p99.9=0.9812 max=2.3151 mean=0.2229 |
| T_addon_first | n=7500 p50=0.1952 p90=0.3645 p99=0.65 p99.9=1.0566 max=2.3151 mean=0.2342 |
| T_release_lag_max | n=763 p50=0.3767 p90=0.7088 p99=0.9046 p99.9=1.3983 max=1.3983 mean=0.4218 |
| T_release_lag_max_arrival | n=763 p50=0.3767 p90=0.7088 p99=0.9046 p99.9=1.3983 max=1.3983 mean=0.4218 |
| T_fw_addon | n=7500 p50=0.2187 p90=0.4459 p99=0.7517 p99.9=1.2568 max=2.3151 mean=0.267 |
| T_fw_addon_sse | n=5250 p50=0.2222 p90=0.4551 p99=0.7567 p99.9=1.0523 max=1.542 mean=0.2695 |
| T_fw_addon_json | n=2250 p50=0.2126 p90=0.417 p99=0.7265 p99.9=1.5911 max=2.3151 mean=0.2611 |
| client_ttft_sse | n=5250 p50=150.3046 p90=150.4884 p99=150.7811 p99.9=150.969 max=151.644 mean=150.3371 |
| provider_sched_err_last | n=7500 p50=0.1104 p90=0.2654 p99=0.3886 p99.9=0.651 max=1.1476 mean=0.1258 |
| provider_sched_err_max_per_stream | n=7500 p50=0.4114 p90=0.6882 p99=0.877 p99.9=1.1137 max=1.16 mean=0.4011 |
| provider_write_max | n=7500 p50=0.018 p90=0.0277 p99=0.042 p99.9=0.0704 max=0.1706 mean=0.0192 |

- release lag: 763 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/v1-bench/runs/D-r25/rv-v1-lg-1/lg', 'samples': 300, 'busy_max': 13.38291300402431, 'busy_mean': 3.9, 'machine': 'c4-standard-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 411, 'busy_max': 15.10792679505486, 'busy_p95': 4.869887574593912}
- completeness: [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/v1-bench/runs/D-r25/rv-v1-lg-1/lg', 'scheduled': 9375, 'recorded': 9375, 'interrupted': False}]
- health olg rv-v1-lg-1: gc_cycles=0 sched_latency_max_ms=0.393216 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 940982, 'TcpInSegs': 1505270}
- health synthprov rv-v1-prov-1: gc_cycles=0 sched_latency_max_ms=0.26214400000000004 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1505404, 'TcpInSegs': 930949}
