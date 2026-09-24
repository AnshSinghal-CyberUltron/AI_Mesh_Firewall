# Harness analysis (sut, policy={'blocks_total': 3112, 'expected_blocks': 0, 'false_positive_blocks': 3112, 'other_blocks': 0, 'benign_offered': 174600, 'false_positive_rate': 0.01782359679266896, 'policy_misses': 0, 'latency_client_total_ms': {'policy_block_fp': {'n': 3112, 'min': 3.4066, 'p50': 7.8004, 'p90': 11.7603, 'p99': 20.784, 'p999': 24.3663, 'max': 27.9849, 'mean': 8.5442}}, 'block_rule': {'statuses': [400, 403, 422, 451], 'envelope_re': '(?i)content_filter|content_polic|polic(y|ies)_|blocked|block_|guard|violation|unsafe|threat'}}) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| infra_error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 174600 over 300 s = 582.0 req/s (configured 582.0)
- qualified: 171488 (571.63 req/s; dispositions ['ALLOW', 'FLAG', 'REDACT'], source auto)
- infra errors (the error-budget gate): 0 (rate 0.0); reasons: {}
- policy stratum (not qualified, not infra errors): expected blocks 0, FALSE-POSITIVE blocks 3112 of 174600 benign (FP rate 0.01782359679266896), other blocks 0, policy misses 0
- policy cohort latency policy_block_fp (client total ms): n=3112 p50=7.8004 p90=11.7603 p99=20.784 p99.9=24.3663 max=27.9849 mean=8.5442
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=174600 p50=0.085 p90=0.0971 p99=0.1125 p99.9=0.1338 max=0.5659 mean=0.0847
- join: {'joined': 171488, 'by_nonce_fallback': 0, 'provider_records': 214384, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=171488 p50=8.3387 p90=12.2609 p99=18.3986 p99.9=24.1976 max=37.3079 mean=8.5925 |
| T_addon_first | n=171488 p50=8.3331 p90=12.5892 p99=25.4186 p99.9=33.1942 max=68.531 mean=8.8061 |
| T_release_lag_max | n=171488 p50=45.6648 p90=52.2387 p99=70.4824 p99.9=80.2381 max=111.1182 mean=36.3326 |
| T_release_lag_max_arrival | n=171488 p50=26.7935 p90=31.8796 p99=39.4327 p99.9=48.6864 max=67.5464 mean=22.8868 |
| T_fw_addon | n=171488 p50=45.6648 p90=52.2387 p99=70.4824 p99.9=80.2381 max=111.1182 mean=36.3326 |
| T_fw_addon_sse | n=120097 p50=48.1762 p90=54.3571 p99=71.1335 p99.9=85.1068 max=111.1182 mean=48.1713 |
| T_fw_addon_json | n=51391 p50=8.4039 p90=12.3439 p99=18.3861 p99.9=24.4996 max=32.7646 mean=8.6665 |
| client_ttft_sse | n=120097 p50=158.3405 p90=162.7506 p99=177.9342 p99.9=184.4659 max=218.5366 mean=158.905 |
| provider_sched_err_last | n=171488 p50=0.0306 p90=0.0857 p99=0.1216 p99.9=0.1561 max=0.7127 mean=0.039 |
| provider_sched_err_max_per_stream | n=171488 p50=0.1157 p90=0.1601 p99=0.2021 p99.9=0.271 max=1.1386 mean=0.1036 |
| provider_write_max | n=171488 p50=0.0214 p90=0.0296 p99=0.0405 p99.9=0.0515 max=0.4029 mean=0.021 |

- release lag: 171488 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/li34-582-r3/rv-split-lg-1/lg', 'samples': 300, 'busy_max': 16.369647725106628, 'busy_mean': 12.49, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-b'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/li34-582-r3/rv-split-lg-2/lg', 'samples': 300, 'busy_max': 19.748302984089506, 'busy_mean': 18.9, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-b'}]
- provider CPU: {'samples': 868, 'busy_max': 20.885859241220437, 'busy_p95': 20.552344175778604}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/li34-582-r3/rv-split-lg-1/lg', 'scheduled': 109125, 'recorded': 109125, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/li34-582-r3/rv-split-lg-2/lg', 'scheduled': 109125, 'recorded': 109125, 'interrupted': False}]
- health olg rv-split-lg-1: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 9587916, 'TcpInSegs': 17023957}
- health olg rv-split-lg-2: gc_cycles=0 sched_latency_max_ms=0.16384 tcp={'TcpRetransSegs': 14, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 5, 'TcpExtTCPTimeouts': 11, 'TcpOutSegs': 9534364, 'TcpInSegs': 17022672}
- health synthprov rv-split-prov-1: gc_cycles=0 sched_latency_max_ms=0.13107200000000002 tcp={'TcpRetransSegs': 3, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 3, 'TcpExtTCPTimeouts': 1, 'TcpOutSegs': 23167279, 'TcpInSegs': 20211431}
- health synthprov rv-split-prov-2: gc_cycles=0 sched_latency_max_ms=0.16384 tcp={'TcpRetransSegs': 4, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 4, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 11575667, 'TcpInSegs': 10077677}
