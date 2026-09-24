# Harness analysis (direct, policy=none) — PASS

| check | result |
|---|---|
| zero_schedule_drops | PASS |
| zero_errors | PASS |
| provider_p99_sched_err_within_tol | PASS |
| loadgen_cpu_max_lt_limit | PASS |
| run_valid | PASS |
| all_joined | PASS |

- offered: 3000 over 300 s = 10.0 req/s (configured 10.0)
- qualified: 3000 (10.0 req/s); expected blocks: 0; errors: 0 (rate 0.0)
- error reasons: {}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=3000 p50=0.0896 p90=0.1238 p99=0.2211 p99.9=0.2587 max=0.29 mean=0.0949
- join: {'joined': 3000, 'by_nonce_fallback': 0, 'provider_records': 3750, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=3000 p50=0.2887 p90=0.5566 p99=0.794 p99.9=0.9615 max=1.5786 mean=0.3303 |
| T_addon_first | n=3000 p50=0.3052 p90=0.6005 p99=0.8284 p99.9=1.0193 max=1.5995 mean=0.357 |
| T_release_lag_max | n=302 p50=0.6764 p90=0.877 p99=1.0396 p99.9=1.1036 max=1.1036 mean=0.6226 |
| T_release_lag_max_arrival | n=302 p50=0.6764 p90=0.877 p99=1.0396 p99.9=1.1036 max=1.1036 mean=0.6226 |
| T_fw_addon | n=3000 p50=0.3652 p90=0.6872 p99=0.9141 p99.9=1.0445 max=1.5995 mean=0.4113 |
| T_fw_addon_sse | n=2100 p50=0.37 p90=0.7085 p99=0.923 p99.9=1.072 max=1.5995 mean=0.419 |
| T_fw_addon_json | n=900 p50=0.3528 p90=0.6266 p99=0.8244 p99.9=1.0445 max=1.0445 mean=0.3932 |
| client_ttft_sse | n=2100 p50=150.3814 p90=150.706 p99=150.9702 p99.9=151.0951 max=151.6532 mean=150.4343 |
| provider_sched_err_last | n=3000 p50=0.0704 p90=0.2264 p99=0.3733 p99.9=0.6008 max=0.6658 mean=0.097 |
| provider_sched_err_max_per_stream | n=3000 p50=0.4103 p90=0.643 p99=0.7871 p99.9=0.864 max=0.882 mean=0.3789 |
| provider_write_max | n=3000 p50=0.0188 p90=0.0304 p99=0.0457 p99.9=0.0729 max=0.0919 mean=0.0206 |

- release lag: 302 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/v1-bench/runs/D-r10/rv-v1-lg-1/lg', 'samples': 300, 'busy_max': 16.013737630226288, 'busy_mean': 4.14, 'machine': 'c4-standard-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 411, 'busy_max': 17.726216030941377, 'busy_p95': 4.643376116391007}
- completeness: [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/v1-bench/runs/D-r10/rv-v1-lg-1/lg', 'scheduled': 3750, 'recorded': 3750, 'interrupted': False}]
- health olg rv-v1-lg-1: gc_cycles=0 sched_latency_max_ms=0.196608 tcp={'TcpRetransSegs': 2, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 1, 'TcpExtTCPTimeouts': 2, 'TcpOutSegs': 435874, 'TcpInSegs': 607603}
- health synthprov rv-v1-prov-1: gc_cycles=0 sched_latency_max_ms=0.26214400000000004 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 607084, 'TcpInSegs': 431340}
