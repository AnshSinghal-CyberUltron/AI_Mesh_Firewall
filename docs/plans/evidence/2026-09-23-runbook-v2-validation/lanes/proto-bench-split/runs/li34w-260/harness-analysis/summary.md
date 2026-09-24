# Harness analysis (sut, policy={'blocks_total': 1868, 'expected_blocks': 0, 'false_positive_blocks': 1868, 'other_blocks': 0, 'benign_offered': 78000, 'false_positive_rate': 0.02394871794871795, 'policy_misses': 0, 'latency_client_total_ms': {'policy_block_fp': {'n': 1868, 'min': 9.5522, 'p50': 10.3276, 'p90': 14.0507, 'p99': 19.1496, 'p999': 24.5188, 'max': 26.2446, 'mean': 11.3654}}, 'block_rule': {'statuses': [400, 403, 422, 451], 'envelope_re': '(?i)content_filter|content_polic|polic(y|ies)_|blocked|block_|guard|violation|unsafe|threat'}}) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| infra_error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 78000 over 300 s = 260.0 req/s (configured 260.0)
- qualified: 76132 (253.77 req/s; dispositions ['ALLOW', 'FLAG', 'REDACT'], source auto)
- infra errors (the error-budget gate): 0 (rate 0.0); reasons: {}
- policy stratum (not qualified, not infra errors): expected blocks 0, FALSE-POSITIVE blocks 1868 of 78000 benign (FP rate 0.02394871794871795), other blocks 0, policy misses 0
- policy cohort latency policy_block_fp (client total ms): n=1868 p50=10.3276 p90=14.0507 p99=19.1496 p99.9=24.5188 max=26.2446 mean=11.3654
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=78000 p50=0.0876 p90=0.0989 p99=0.1123 p99.9=0.1327 max=0.485 mean=0.0871
- join: {'joined': 76132, 'by_nonce_fallback': 0, 'provider_records': 95134, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=76132 p50=11.3121 p90=14.4397 p99=19.2084 p99.9=24.1812 max=34.3213 mean=12.1405 |
| T_addon_first | n=76132 p50=11.3072 p90=14.8218 p99=30.5928 p99.9=35.4462 max=55.7111 mean=12.3703 |
| T_release_lag_max | n=76132 p50=50.8842 p90=56.211 p99=73.8141 p99.9=90.4195 max=113.3297 mean=41.5268 |
| T_release_lag_max_arrival | n=76132 p50=31.0816 p90=34.1425 p99=39.3212 p99.9=47.0982 max=61.2191 mean=26.3349 |
| T_fw_addon | n=76132 p50=50.8842 p90=56.211 p99=73.8141 p99.9=90.4195 max=113.3297 mean=41.5268 |
| T_fw_addon_sse | n=53301 p50=51.2435 p90=70.2272 p99=74.6767 p99.9=90.9098 max=113.3297 mean=54.0334 |
| T_fw_addon_json | n=22831 p50=11.4502 p90=14.5672 p99=19.2925 p99.9=24.1893 max=32.1887 mean=12.3289 |
| client_ttft_sse | n=53301 p50=161.2513 p90=165.0417 p99=181.0161 p99.9=186.2477 max=205.8003 mean=162.4284 |
| provider_sched_err_last | n=76132 p50=0.0343 p90=0.0854 p99=0.1166 p99.9=0.145 max=0.1988 mean=0.0406 |
| provider_sched_err_max_per_stream | n=76132 p50=0.1137 p90=0.1541 p99=0.1934 p99.9=0.2222 max=0.493 mean=0.1015 |
| provider_write_max | n=76132 p50=0.0251 p90=0.0355 p99=0.048 p99.9=0.058 max=0.1078 mean=0.0265 |

- release lag: 76132 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/li34w-260/rv-split-lg-1/lg', 'samples': 300, 'busy_max': 13.729761922944094, 'busy_mean': 10.07, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-b'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/li34w-260/rv-split-lg-2/lg', 'samples': 300, 'busy_max': 16.084997463068206, 'busy_mean': 15.46, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-b'}]
- provider CPU: {'samples': 868, 'busy_max': 24.135352166705637, 'busy_p95': 17.20525499324802}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/li34w-260/rv-split-lg-1/lg', 'scheduled': 48750, 'recorded': 48750, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/li34w-260/rv-split-lg-2/lg', 'scheduled': 48750, 'recorded': 48750, 'interrupted': False}]
- health olg rv-split-lg-1: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 1, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 1, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 7387861, 'TcpInSegs': 13243496}
- health olg rv-split-lg-2: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 12, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 3, 'TcpExtTCPTimeouts': 10, 'TcpOutSegs': 7374689, 'TcpInSegs': 13248237}
- health synthprov rv-split-prov-1: gc_cycles=0 sched_latency_max_ms=0.13107200000000002 tcp={'TcpRetransSegs': 5, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 6, 'TcpOutSegs': 18008164, 'TcpInSegs': 14666660}
- health synthprov rv-split-prov-2: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 3, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 3, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 8985203, 'TcpInSegs': 7297785}
