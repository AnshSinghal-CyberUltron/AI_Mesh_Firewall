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
- schedule drops (>5.0 ms): 0; lateness ms: n=1800 p50=0.0967 p90=0.1175 p99=0.1429 p99.9=0.1931 max=0.2685 mean=0.0993
- join: {'joined': 1800, 'by_nonce_fallback': 0, 'provider_records': 2150, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=1800 p50=194.6915 p90=659.518 p99=1648.5761 p99.9=2452.1028 max=2574.7836 mean=316.0765 |
| T_addon_first | n=1800 p50=421.3789 p90=523.7291 p99=619.8903 p99.9=656.851 max=810.538 mean=420.3317 |
| T_release_lag_max | n=183 p50=599.9 p90=827.7318 p99=2013.6716 p99.9=2603.1323 max=2603.1323 mean=665.8621 |
| T_release_lag_max_arrival | n=183 p50=599.9 p90=827.7318 p99=2013.6716 p99.9=2603.1323 max=2603.1323 mean=665.8621 |
| T_fw_addon | n=1800 p50=439.6116 p90=703.3391 p99=1648.5761 p99.9=2452.1028 max=2603.1323 mean=513.6927 |
| T_fw_addon_sse | n=1800 p50=439.6116 p90=703.3391 p99=1648.5761 p99.9=2452.1028 max=2603.1323 mean=513.6927 |
| T_fw_addon_json | n=0 |
| client_ttft_sse | n=1800 p50=571.4024 p90=673.7959 p99=769.9775 p99.9=806.9144 max=960.6248 mean=570.3841 |
| provider_sched_err_last | n=1800 p50=0.0509 p90=0.0883 p99=0.1007 p99.9=0.1073 max=0.1152 mean=0.052 |
| provider_sched_err_max_per_stream | n=1800 p50=0.0628 p90=0.0999 p99=0.129 p99.9=0.1909 max=0.2229 mean=0.0646 |
| provider_write_max | n=1800 p50=0.0149 p90=0.0198 p99=0.0261 p99.9=0.0477 max=0.0578 mean=0.0153 |

- release lag: 183 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/v1-bench/runs/ITL10-sse-r10/lg-v1aug', 'samples': 180, 'busy_max': 7.198873993664745, 'busy_mean': 3.24, 'machine': 'c4-standard-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 238, 'busy_max': 16.74751355802825, 'busy_p95': 5.722986260221486}
- completeness: [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/v1-bench/runs/ITL10-sse-r10/lg-v1aug', 'scheduled': 2150, 'recorded': 2150, 'interrupted': False}]
- health olg ITL10-sse-r10: gc_cycles=0 sched_latency_max_ms=0.098304 tcp=None
- health synthprov rv-v1-prov-2: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 1046, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 1046, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 495267, 'TcpInSegs': 490964}
