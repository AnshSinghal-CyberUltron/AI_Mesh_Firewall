# Harness analysis (sut, policy={'blocks_total': 1241, 'expected_blocks': 0, 'false_positive_blocks': 1241, 'other_blocks': 0, 'benign_offered': 71400, 'false_positive_rate': 0.017380952380952382, 'policy_misses': 0, 'latency_client_total_ms': {'policy_block_fp': {'n': 1241, 'min': 3.1224, 'p50': 6.9741, 'p90': 9.2893, 'p99': 10.9307, 'p999': 11.8043, 'max': 12.2074, 'mean': 6.8007}}, 'block_rule': {'statuses': [400, 403, 422, 451], 'envelope_re': '(?i)content_filter|content_polic|polic(y|ies)_|blocked|block_|guard|violation|unsafe|threat'}}) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| infra_error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 71400 over 300 s = 238.0 req/s (configured 238.0)
- qualified: 70159 (233.86 req/s; dispositions ['ALLOW', 'FLAG', 'REDACT'], source auto)
- infra errors (the error-budget gate): 0 (rate 0.0); reasons: {}
- policy stratum (not qualified, not infra errors): expected blocks 0, FALSE-POSITIVE blocks 1241 of 71400 benign (FP rate 0.017380952380952382), other blocks 0, policy misses 0
- policy cohort latency policy_block_fp (client total ms): n=1241 p50=6.9741 p90=9.2893 p99=10.9307 p99.9=11.8043 max=12.2074 mean=6.8007
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=71400 p50=0.0834 p90=0.0946 p99=0.1091 p99.9=0.126 max=0.3377 mean=0.0828
- join: {'joined': 70159, 'by_nonce_fallback': 0, 'provider_records': 87702, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=70159 p50=7.1871 p90=9.6003 p99=11.3974 p99.9=13.2268 max=16.6659 mean=6.7187 |
| T_addon_first | n=70159 p50=7.1895 p90=9.7609 p99=24.2386 p99.9=29.7287 max=49.7781 mean=6.9224 |
| T_release_lag_max | n=70159 p50=44.7106 p90=49.6735 p99=67.8042 p99.9=71.8457 max=107.3597 mean=34.5784 |
| T_release_lag_max_arrival | n=70159 p50=27.3529 p90=31.0184 p99=33.7501 p99.9=35.6728 max=39.2603 mean=22.2419 |
| T_fw_addon | n=70159 p50=44.7106 p90=49.6735 p99=67.8042 p99.9=71.8457 max=107.3597 mean=34.5784 |
| T_fw_addon_sse | n=49127 p50=47.0881 p90=50.2387 p99=68.1181 p99.9=84.3692 max=107.3597 mean=46.4778 |
| T_fw_addon_json | n=21032 p50=7.2473 p90=9.7199 p99=11.5298 p99.9=13.3744 max=16.6659 mean=6.7833 |
| client_ttft_sse | n=49127 p50=157.2047 p90=159.8154 p99=175.6011 p99.9=180.0343 max=199.7828 mean=157.022 |
| provider_sched_err_last | n=70159 p50=0.0322 p90=0.0856 p99=0.1173 p99.9=0.1431 max=0.2143 mean=0.0397 |
| provider_sched_err_max_per_stream | n=70159 p50=0.107 p90=0.1484 p99=0.1886 p99.9=0.2186 max=0.9176 mean=0.0968 |
| provider_write_max | n=70159 p50=0.0206 p90=0.0283 p99=0.0398 p99.9=0.0513 max=0.4248 mean=0.0202 |

- release lag: 70159 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s2-238/rv-split-lg-1/lg', 'samples': 300, 'busy_max': 26.0158681633914, 'busy_mean': 15.53, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 27.259268190245546, 'busy_p95': 15.381899969748558}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s2-238/rv-split-lg-1/lg', 'scheduled': 89250, 'recorded': 89250, 'interrupted': False}]
- health olg rv-split-lg-1: gc_cycles=0 sched_latency_max_ms=0.16384 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 7987933, 'TcpInSegs': 13854225}
- health synthprov rv-split-prov-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 1, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 1, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 14204496, 'TcpInSegs': 11620731}
