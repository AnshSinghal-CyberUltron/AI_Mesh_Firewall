# Harness analysis (sut, policy={'blocks_total': 917, 'expected_blocks': 0, 'false_positive_blocks': 917, 'other_blocks': 0, 'benign_offered': 46259, 'false_positive_rate': 0.019823169545385762, 'policy_misses': 0, 'latency_client_total_ms': {'policy_block_fp': {'n': 917, 'min': 3.2445, 'p50': 8.9509, 'p90': 22.6946, 'p99': 25.6887, 'p999': 27.4395, 'max': 27.4395, 'mean': 10.9798}}, 'block_rule': {'statuses': [400, 403, 422, 451], 'envelope_re': '(?i)content_filter|content_polic|polic(y|ies)_|blocked|block_|guard|violation|unsafe|threat'}}) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| infra_error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 46259 over 300 s = 154.2 req/s (configured 153.0)
- qualified: 45342 (151.14 req/s; dispositions ['ALLOW', 'FLAG', 'REDACT'], source auto)
- infra errors (the error-budget gate): 0 (rate 0.0); reasons: {}
- policy stratum (not qualified, not infra errors): expected blocks 0, FALSE-POSITIVE blocks 917 of 46259 benign (FP rate 0.019823169545385762), other blocks 0, policy misses 0
- policy cohort latency policy_block_fp (client total ms): n=917 p50=8.9509 p90=22.6946 p99=25.6887 p99.9=27.4395 max=27.4395 mean=10.9798
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=46259 p50=0.0827 p90=0.0933 p99=0.1059 p99.9=0.121 max=0.1656 mean=0.0813
- join: {'joined': 45342, 'by_nonce_fallback': 0, 'provider_records': 56557, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=45342 p50=7.861 p90=14.2744 p99=22.6873 p99.9=27.0216 max=39.4601 mean=8.8902 |
| T_addon_first | n=45342 p50=7.876 p90=14.6749 p99=26.2877 p99.9=35.7067 max=54.9755 mean=9.1041 |
| T_release_lag_max | n=45342 p50=46.031 p90=54.4206 p99=70.7686 p99.9=84.0642 max=108.9086 mean=36.7336 |
| T_release_lag_max_arrival | n=45342 p50=28.4458 p90=35.1607 p99=44.3351 p99.9=49.3446 max=60.5053 mean=24.4066 |
| T_fw_addon | n=45342 p50=46.031 p90=54.4206 p99=70.7686 p99.9=84.0642 max=108.9086 mean=36.7336 |
| T_fw_addon_sse | n=31758 p50=47.7746 p90=57.2436 p99=72.2587 p99.9=85.2968 max=108.9086 mean=48.623 |
| T_fw_addon_json | n=13584 p50=7.9122 p90=14.3292 p99=22.6956 p99.9=26.758 max=39.4601 mean=8.9374 |
| client_ttft_sse | n=31758 p50=157.901 p90=164.9077 p99=177.3931 p99.9=187.1169 max=204.9802 mean=159.2184 |
| provider_sched_err_last | n=45342 p50=0.0378 p90=0.087 p99=0.115 p99.9=0.1387 max=0.2089 mean=0.0428 |
| provider_sched_err_max_per_stream | n=45342 p50=0.0973 p90=0.138 p99=0.1776 p99.9=0.2045 max=0.2732 mean=0.0906 |
| provider_write_max | n=45342 p50=0.0199 p90=0.0268 p99=0.038 p99.9=0.0488 max=0.2843 mean=0.0195 |

- release lag: 45342 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s1p-153/rv-split-lg-1/lg', 'samples': 300, 'busy_max': 14.66691881102501, 'busy_mean': 11.28, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 12.553933658351458, 'busy_p95': 12.10865699323863}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s1p-153/rv-split-lg-1/lg', 'scheduled': 57679, 'recorded': 57679, 'interrupted': False}]
- health olg rv-split-lg-1: gc_cycles=0 sched_latency_max_ms=0.196608 tcp={'TcpRetransSegs': 1, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 1, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 5365351, 'TcpInSegs': 8934912}
- health synthprov rv-split-prov-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 12, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 12, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 9161127, 'TcpInSegs': 7815799}
