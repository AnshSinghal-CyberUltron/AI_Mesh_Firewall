# Harness analysis (sut, policy={'blocks_total': 1585, 'expected_blocks': 0, 'false_positive_blocks': 1585, 'other_blocks': 0, 'benign_offered': 89400, 'false_positive_rate': 0.01772930648769575, 'policy_misses': 0, 'latency_client_total_ms': {'policy_block_fp': {'n': 1585, 'min': 3.2117, 'p50': 7.5882, 'p90': 10.2835, 'p99': 13.7209, 'p999': 16.9753, 'max': 18.7905, 'mean': 7.7354}}, 'block_rule': {'statuses': [400, 403, 422, 451], 'envelope_re': '(?i)content_filter|content_polic|polic(y|ies)_|blocked|block_|guard|violation|unsafe|threat'}}) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| infra_error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 89400 over 300 s = 298.0 req/s (configured 298.0)
- qualified: 87815 (292.72 req/s; dispositions ['ALLOW', 'FLAG', 'REDACT'], source auto)
- infra errors (the error-budget gate): 0 (rate 0.0); reasons: {}
- policy stratum (not qualified, not infra errors): expected blocks 0, FALSE-POSITIVE blocks 1585 of 89400 benign (FP rate 0.01772930648769575), other blocks 0, policy misses 0
- policy cohort latency policy_block_fp (client total ms): n=1585 p50=7.5882 p90=10.2835 p99=13.7209 p99.9=16.9753 max=18.7905 mean=7.7354
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=89400 p50=0.0818 p90=0.0908 p99=0.1017 p99.9=0.1175 max=0.2724 mean=0.0811
- join: {'joined': 87815, 'by_nonce_fallback': 0, 'provider_records': 109789, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=87815 p50=8.0618 p90=11.0225 p99=14.2027 p99.9=17.4586 max=37.2672 mean=7.9592 |
| T_addon_first | n=87815 p50=8.039 p90=11.117 p99=24.7226 p99.9=31.164 max=66.3905 mean=8.1618 |
| T_release_lag_max | n=87815 p50=45.9573 p90=51.2132 p99=69.3746 p99.9=75.209 max=110.0822 mean=35.932 |
| T_release_lag_max_arrival | n=87815 p50=29.7281 p90=34.1372 p99=37.8812 p99.9=45.6523 max=62.0545 mean=24.3573 |
| T_fw_addon | n=87815 p50=45.9573 p90=51.2132 p99=69.3746 p99.9=75.209 max=110.0822 mean=35.932 |
| T_fw_addon_sse | n=61497 p50=48.0178 p90=52.2443 p99=70.2926 p99.9=84.9921 max=110.0822 mean=47.8799 |
| T_fw_addon_json | n=26318 p50=8.1105 p90=11.0706 p99=14.2108 p99.9=17.3839 max=24.1518 mean=8.0133 |
| client_ttft_sse | n=61497 p50=158.0474 p90=161.1746 p99=177.2583 p99.9=181.8446 max=216.4352 mean=158.2644 |
| provider_sched_err_last | n=87815 p50=0.0307 p90=0.0845 p99=0.1194 p99.9=0.15 max=0.2111 mean=0.0388 |
| provider_sched_err_max_per_stream | n=87815 p50=0.1139 p90=0.1568 p99=0.1966 p99.9=0.2277 max=0.5114 mean=0.1017 |
| provider_write_max | n=87815 p50=0.0212 p90=0.029 p99=0.0394 p99.9=0.0498 max=0.0935 mean=0.0208 |

- release lag: 87815 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s2b-298/rv-split-lg-1/lg', 'samples': 300, 'busy_max': 17.0489118866194, 'busy_mean': 12.83, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-b'}]
- provider CPU: {'samples': 431, 'busy_max': 27.973110355069643, 'busy_p95': 17.375850414924642}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s2b-298/rv-split-lg-1/lg', 'scheduled': 111750, 'recorded': 111750, 'interrupted': False}]
- health olg rv-split-lg-1: gc_cycles=0 sched_latency_max_ms=0.16384 tcp={'TcpRetransSegs': 9, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 9, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 10145094, 'TcpInSegs': 17361760}
- health synthprov rv-split-prov-1: gc_cycles=0 sched_latency_max_ms=0.13107200000000002 tcp={'TcpRetransSegs': 45, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 40, 'TcpExtTCPTimeouts': 6, 'TcpOutSegs': 17789728, 'TcpInSegs': 14225295}
