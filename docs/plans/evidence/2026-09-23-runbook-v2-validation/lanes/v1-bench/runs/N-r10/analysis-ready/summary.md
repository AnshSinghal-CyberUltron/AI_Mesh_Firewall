# Harness analysis (sut, policy={'blocks_total': 0, 'expected_blocks': 0, 'false_positive_blocks': 0, 'other_blocks': 0, 'benign_offered': 3000, 'false_positive_rate': 0.0, 'policy_misses': 0, 'latency_client_total_ms': {}, 'block_rule': {'statuses': [400, 403, 422, 451], 'envelope_re': '(?i)content_filter|content_polic|polic(y|ies)_|blocked|block_|guard|violation|unsafe|threat'}}) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| infra_error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 3000 over 300 s = 10.0 req/s (configured 10.0)
- qualified: 3000 (10.0 req/s; dispositions ['ALLOW', 'FLAG', 'REDACT'], source v1)
- infra errors (the error-budget gate): 0 (rate 0.0); reasons: {}
- policy stratum (not qualified, not infra errors): expected blocks 0, FALSE-POSITIVE blocks 0 of 3000 benign (FP rate 0.0), other blocks 0, policy misses 0
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=3000 p50=0.0968 p90=0.1204 p99=0.1805 p99.9=0.2565 max=0.2786 mean=0.1005
- join: {'joined': 3000, 'by_nonce_fallback': 0, 'provider_records': 3750, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=3000 p50=165.681 p90=790.9784 p99=1959.4046 p99.9=2593.4394 max=2702.5955 mean=320.4821 |
| T_addon_first | n=3000 p50=729.81 p90=922.5962 p99=1126.464 p99.9=1295.0831 max=1446.9791 mean=583.4616 |
| T_release_lag_max | n=281 p50=1119.0783 p90=1365.0227 p99=1916.2381 p99.9=2641.6912 max=2641.6912 mean=920.7476 |
| T_release_lag_max_arrival | n=281 p50=1119.0783 p90=1365.0227 p99=1916.2381 p99.9=2641.6912 max=2641.6912 mean=920.7476 |
| T_fw_addon | n=3000 p50=749.4985 p90=1117.7389 p99=1959.4046 p99.9=2621.0258 max=2702.5955 mean=654.9931 |
| T_fw_addon_sse | n=2100 p50=806.8308 p90=1174.5305 p99=2089.0524 p99.9=2623.454 max=2702.5955 mean=878.3238 |
| T_fw_addon_json | n=900 p50=126.6404 p90=192.7821 p99=350.832 p99.9=537.6577 max=537.6577 mean=133.888 |
| client_ttft_sse | n=2100 p50=931.5927 p90=1125.557 p99=1295.1918 p99.9=1488.3998 max=1597.055 mean=926.1888 |
| provider_sched_err_last | n=3000 p50=0.051 p90=0.0899 p99=0.1001 p99.9=0.1051 max=0.1084 mean=0.0518 |
| provider_sched_err_max_per_stream | n=3000 p50=0.0591 p90=0.0991 p99=0.1221 p99.9=0.1814 max=0.264 mean=0.0606 |
| provider_write_max | n=3000 p50=0.0144 p90=0.0211 p99=0.0341 p99.9=0.0534 max=0.0744 mean=0.0152 |

- release lag: 281 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/v1-bench/runs/N-r10/lg-v1ready', 'samples': 300, 'busy_max': 15.347820170989102, 'busy_mean': 3.13, 'machine': 'c4-standard-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 412, 'busy_max': 16.8237852977577, 'busy_p95': 3.8753040157917473}
- completeness: [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/v1-bench/runs/N-r10/lg-v1ready', 'scheduled': 3750, 'recorded': 3750, 'interrupted': False}]
- health olg N-r10: gc_cycles=0 sched_latency_max_ms=0.114688 tcp=None
- health synthprov rv-v1-prov-2: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 1453, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 1453, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 607405, 'TcpInSegs': 604026}
