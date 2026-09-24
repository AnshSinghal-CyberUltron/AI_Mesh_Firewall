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
- schedule drops (>5.0 ms): 0; lateness ms: n=1800 p50=0.1053 p90=0.1268 p99=0.1512 p99.9=0.1936 max=0.2485 mean=0.1068
- join: {'joined': 1800, 'by_nonce_fallback': 0, 'provider_records': 2150, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=1800 p50=239.0051 p90=741.8596 p99=1063.6671 p99.9=1273.345 max=1279.9839 mean=346.1053 |
| T_addon_first | n=1800 p50=785.0409 p90=979.8878 p99=1143.0056 p99.9=1373.5718 max=1379.1129 mean=779.0022 |
| T_release_lag_max | n=171 p50=1145.335 p90=1300.6572 p99=1523.7014 p99.9=1645.5119 max=1645.5119 mean=1159.7247 |
| T_release_lag_max_arrival | n=171 p50=1145.335 p90=1300.6572 p99=1523.7014 p99.9=1645.5119 max=1645.5119 mean=1159.7247 |
| T_fw_addon | n=1800 p50=801.5765 p90=1099.3802 p99=1313.2491 p99.9=1523.7014 max=1645.5119 mean=825.8889 |
| T_fw_addon_sse | n=1800 p50=801.5765 p90=1099.3802 p99=1313.2491 p99.9=1523.7014 max=1645.5119 mean=825.8889 |
| T_fw_addon_json | n=0 |
| client_ttft_sse | n=1800 p50=935.0603 p90=1129.9785 p99=1293.0387 p99.9=1523.6477 max=1529.169 mean=929.0536 |
| provider_sched_err_last | n=1800 p50=0.0494 p90=0.0897 p99=0.1002 p99.9=0.1114 max=0.1118 mean=0.0513 |
| provider_sched_err_max_per_stream | n=1800 p50=0.0624 p90=0.1008 p99=0.1281 p99.9=0.1806 max=0.1998 mean=0.0637 |
| provider_write_max | n=1800 p50=0.0152 p90=0.0225 p99=0.0349 p99.9=0.0659 max=0.1095 mean=0.0161 |

- release lag: 171 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/v1-bench/runs/ITL20-sse-r10/lg-v1aug', 'samples': 180, 'busy_max': 7.253665542873133, 'busy_mean': 3.24, 'machine': 'c4-standard-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 238, 'busy_max': 14.245557792603547, 'busy_p95': 3.913591703949304}
- completeness: [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/v1-bench/runs/ITL20-sse-r10/lg-v1aug', 'scheduled': 2150, 'recorded': 2150, 'interrupted': False}]
- health olg ITL20-sse-r10: gc_cycles=0 sched_latency_max_ms=0.098304 tcp=None
- health synthprov rv-v1-prov-2: gc_cycles=0 sched_latency_max_ms=0.08192 tcp={'TcpRetransSegs': 1218, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 1218, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 495223, 'TcpInSegs': 492687}
