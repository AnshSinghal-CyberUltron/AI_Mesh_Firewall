# Harness analysis (sut, policy={'blocks_total': 1995, 'expected_blocks': 0, 'false_positive_blocks': 1995, 'other_blocks': 0, 'benign_offered': 111900, 'false_positive_rate': 0.017828418230563, 'policy_misses': 0, 'latency_client_total_ms': {'policy_block_fp': {'n': 1995, 'min': 3.3668, 'p50': 8.1151, 'p90': 11.6153, 'p99': 15.429, 'p999': 24.1692, 'max': 32.6399, 'mean': 8.5143}}, 'block_rule': {'statuses': [400, 403, 422, 451], 'envelope_re': '(?i)content_filter|content_polic|polic(y|ies)_|blocked|block_|guard|violation|unsafe|threat'}}) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| infra_error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 111900 over 300 s = 373.0 req/s (configured 373.0)
- qualified: 109905 (366.35 req/s; dispositions ['ALLOW', 'FLAG', 'REDACT'], source auto)
- infra errors (the error-budget gate): 0 (rate 0.0); reasons: {}
- policy stratum (not qualified, not infra errors): expected blocks 0, FALSE-POSITIVE blocks 1995 of 111900 benign (FP rate 0.017828418230563), other blocks 0, policy misses 0
- policy cohort latency policy_block_fp (client total ms): n=1995 p50=8.1151 p90=11.6153 p99=15.429 p99.9=24.1692 max=32.6399 mean=8.5143
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=111900 p50=0.0816 p90=0.0909 p99=0.1029 p99.9=0.1201 max=0.7449 mean=0.0807
- join: {'joined': 109905, 'by_nonce_fallback': 0, 'provider_records': 137392, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=109905 p50=8.689 p90=12.4593 p99=16.7136 p99.9=20.5858 max=45.5746 mean=8.8988 |
| T_addon_first | n=109905 p50=8.676 p90=12.6603 p99=25.7089 p99.9=33.3019 max=54.7618 mean=9.1007 |
| T_release_lag_max | n=109905 p50=47.1332 p90=52.8 p99=70.6477 p99.9=79.6878 max=131.541 mean=36.8958 |
| T_release_lag_max_arrival | n=109905 p50=30.1349 p90=34.8498 p99=40.7579 p99.9=57.4853 max=68.6856 mean=25.0039 |
| T_fw_addon | n=109905 p50=47.1332 p90=52.8 p99=70.6477 p99.9=79.6878 max=131.541 mean=36.8958 |
| T_fw_addon_sse | n=76970 p50=48.7335 p90=54.2423 p99=71.417 p99.9=87.1943 max=131.541 mean=48.8563 |
| T_fw_addon_json | n=32935 p50=8.7249 p90=12.5296 p99=16.7237 p99.9=21.2271 max=43.3593 mean=8.9438 |
| client_ttft_sse | n=76970 p50=158.6895 p90=162.7526 p99=178.2036 p99.9=184.4521 max=204.8464 mean=159.2064 |
| provider_sched_err_last | n=109905 p50=0.0288 p90=0.0862 p99=0.123 p99.9=0.1568 max=0.261 mean=0.0384 |
| provider_sched_err_max_per_stream | n=109905 p50=0.1208 p90=0.1644 p99=0.2051 p99.9=0.2725 max=0.6853 mean=0.1067 |
| provider_write_max | n=109905 p50=0.0213 p90=0.029 p99=0.0405 p99.9=0.052 max=0.1058 mean=0.0209 |

- release lag: 109905 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s2b-373-nodump/rv-split-lg-1/lg', 'samples': 300, 'busy_max': 28.002670140528906, 'busy_mean': 15.77, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-b'}]
- provider CPU: {'samples': 431, 'busy_max': 30.36535005859986, 'busy_p95': 19.64383190525012}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s2b-373-nodump/rv-split-lg-1/lg', 'scheduled': 139875, 'recorded': 139875, 'interrupted': False}]
- health olg rv-split-lg-1: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 7, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 2, 'TcpExtTCPTimeouts': 6, 'TcpOutSegs': 12693974, 'TcpInSegs': 21732113}
- health synthprov rv-split-prov-1: gc_cycles=0 sched_latency_max_ms=0.13107200000000002 tcp={'TcpRetransSegs': 38, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 38, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 22262645, 'TcpInSegs': 17298937}
