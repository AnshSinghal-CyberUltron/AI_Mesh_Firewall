# Harness analysis (sut, policy={'blocks_total': 1255, 'expected_blocks': 0, 'false_positive_blocks': 1255, 'other_blocks': 0, 'benign_offered': 71400, 'false_positive_rate': 0.01757703081232493, 'policy_misses': 0, 'latency_client_total_ms': {'policy_block_fp': {'n': 1255, 'min': 3.4096, 'p50': 8.1991, 'p90': 13.831, 'p99': 23.3182, 'p999': 25.6694, 'max': 26.8139, 'mean': 9.2716}}, 'block_rule': {'statuses': [400, 403, 422, 451], 'envelope_re': '(?i)content_filter|content_polic|polic(y|ies)_|blocked|block_|guard|violation|unsafe|threat'}}) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| infra_error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 71400 over 300 s = 238.0 req/s (configured 238.0)
- qualified: 70145 (233.82 req/s; dispositions ['ALLOW', 'FLAG', 'REDACT'], source auto)
- infra errors (the error-budget gate): 0 (rate 0.0); reasons: {}
- policy stratum (not qualified, not infra errors): expected blocks 0, FALSE-POSITIVE blocks 1255 of 71400 benign (FP rate 0.01757703081232493), other blocks 0, policy misses 0
- policy cohort latency policy_block_fp (client total ms): n=1255 p50=8.1991 p90=13.831 p99=23.3182 p99.9=25.6694 max=26.8139 mean=9.2716
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=71400 p50=0.0836 p90=0.0949 p99=0.1101 p99.9=0.1285 max=0.3682 mean=0.0832
- join: {'joined': 70145, 'by_nonce_fallback': 0, 'provider_records': 87678, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=70145 p50=8.4851 p90=13.6499 p99=20.8619 p99.9=25.5291 max=36.4093 mean=9.2245 |
| T_addon_first | n=70145 p50=8.4735 p90=13.984 p99=26.1808 p99.9=34.5765 max=55.5813 mean=9.4298 |
| T_release_lag_max | n=70145 p50=47.02 p90=53.797 p99=70.8756 p99.9=81.2735 max=111.521 mean=37.1115 |
| T_release_lag_max_arrival | n=70145 p50=29.4535 p90=34.9955 p99=42.8124 p99.9=49.6042 max=59.6877 mean=24.889 |
| T_fw_addon | n=70145 p50=47.02 p90=53.797 p99=70.8756 p99.9=81.2735 max=111.521 mean=37.1115 |
| T_fw_addon_sse | n=49114 p50=48.4405 p90=56.0854 p99=71.7519 p99.9=85.5703 max=111.521 mean=49.023 |
| T_fw_addon_json | n=21031 p50=8.5653 p90=13.7003 p99=21.0479 p99.9=25.5594 max=31.4279 mean=9.2945 |
| client_ttft_sse | n=49114 p50=158.4762 p90=164.1703 p99=177.9922 p99.9=186.4357 max=205.5884 mean=159.5282 |
| provider_sched_err_last | n=70145 p50=0.0334 p90=0.0855 p99=0.1167 p99.9=0.1421 max=0.2141 mean=0.0403 |
| provider_sched_err_max_per_stream | n=70145 p50=0.1074 p90=0.1493 p99=0.1911 p99.9=0.2189 max=0.649 mean=0.0975 |
| provider_write_max | n=70145 p50=0.0207 p90=0.0288 p99=0.0396 p99.9=0.0497 max=0.1231 mean=0.0204 |

- release lag: 70145 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s1-238/rv-split-lg-1/lg', 'samples': 300, 'busy_max': 18.313939833854008, 'busy_mean': 15.35, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 22.412793836817546, 'busy_p95': 15.414370044981684}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s1-238/rv-split-lg-1/lg', 'scheduled': 89250, 'recorded': 89250, 'interrupted': False}]
- health olg rv-split-lg-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 1, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 1, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 7983926, 'TcpInSegs': 13850181}
- health synthprov rv-split-prov-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 4, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 4, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 14200426, 'TcpInSegs': 11627191}
