# Harness analysis (sut, policy={'blocks_total': 2738, 'expected_blocks': 0, 'false_positive_blocks': 2738, 'other_blocks': 0, 'benign_offered': 156000, 'false_positive_rate': 0.017551282051282053, 'policy_misses': 0, 'latency_client_total_ms': {'policy_block_fp': {'n': 2738, 'min': 3.2502, 'p50': 7.6748, 'p90': 10.8356, 'p99': 17.3588, 'p999': 24.6817, 'max': 28.6855, 'mean': 8.2041}}, 'block_rule': {'statuses': [400, 403, 422, 451], 'envelope_re': '(?i)content_filter|content_polic|polic(y|ies)_|blocked|block_|guard|violation|unsafe|threat'}}) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| infra_error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 156000 over 300 s = 520.0 req/s (configured 520.0)
- qualified: 153262 (510.87 req/s; dispositions ['ALLOW', 'FLAG', 'REDACT'], source auto)
- infra errors (the error-budget gate): 0 (rate 0.0); reasons: {}
- policy stratum (not qualified, not infra errors): expected blocks 0, FALSE-POSITIVE blocks 2738 of 156000 benign (FP rate 0.017551282051282053), other blocks 0, policy misses 0
- policy cohort latency policy_block_fp (client total ms): n=2738 p50=7.6748 p90=10.8356 p99=17.3588 p99.9=24.6817 max=28.6855 mean=8.2041
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=156000 p50=0.0855 p90=0.0967 p99=0.1105 p99.9=0.1332 max=0.3344 mean=0.0858
- join: {'joined': 153262, 'by_nonce_fallback': 0, 'provider_records': 191602, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=153262 p50=8.1726 p90=11.4396 p99=16.7788 p99.9=21.3188 max=38.5729 mean=8.1928 |
| T_addon_first | n=153262 p50=8.1718 p90=11.5971 p99=25.1194 p99.9=32.6749 max=69.7883 mean=8.4105 |
| T_release_lag_max | n=153262 p50=45.4399 p90=51.3471 p99=69.8396 p99.9=77.8874 max=105.0816 mean=35.9114 |
| T_release_lag_max_arrival | n=153262 p50=26.1204 p90=31.2142 p99=36.5686 p99.9=42.8553 max=64.717 mean=22.3779 |
| T_fw_addon | n=153262 p50=45.4399 p90=51.3471 p99=69.8396 p99.9=77.8874 max=105.0816 mean=35.9114 |
| T_fw_addon_sse | n=107301 p50=48.01 p90=53.2175 p99=70.7651 p99.9=84.6949 max=105.0816 mean=47.7535 |
| T_fw_addon_json | n=45961 p50=8.2318 p90=11.5101 p99=16.8266 p99.9=21.2556 max=30.8252 mean=8.2648 |
| client_ttft_sse | n=107301 p50=158.1853 p90=161.6958 p99=177.7396 p99.9=183.955 max=219.8324 mean=158.5127 |
| provider_sched_err_last | n=153262 p50=0.0316 p90=0.0859 p99=0.1198 p99.9=0.152 max=0.2139 mean=0.0396 |
| provider_sched_err_max_per_stream | n=153262 p50=0.1127 p90=0.1565 p99=0.1977 p99.9=0.2299 max=0.6981 mean=0.1012 |
| provider_write_max | n=153262 p50=0.0211 p90=0.0289 p99=0.0402 p99.9=0.0507 max=0.1148 mean=0.0207 |

- release lag: 153262 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/li34-520/rv-split-lg-1/lg', 'samples': 300, 'busy_max': 22.11220964652196, 'busy_mean': 11.36, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-b'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/li34-520/rv-split-lg-2/lg', 'samples': 300, 'busy_max': 27.45120469119048, 'busy_mean': 17.23, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-b'}]
- provider CPU: {'samples': 868, 'busy_max': 30.070768607060216, 'busy_p95': 19.343604956543935}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/li34-520/rv-split-lg-1/lg', 'scheduled': 97500, 'recorded': 97500, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/li34-520/rv-split-lg-2/lg', 'scheduled': 97500, 'recorded': 97500, 'interrupted': False}]
- health olg rv-split-lg-1: gc_cycles=0 sched_latency_max_ms=0.16384 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 8568359, 'TcpInSegs': 15208305}
- health olg rv-split-lg-2: gc_cycles=0 sched_latency_max_ms=0.13107200000000002 tcp={'TcpRetransSegs': 16, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 1, 'TcpExtTCPTimeouts': 17, 'TcpOutSegs': 8567242, 'TcpInSegs': 15210127}
- health synthprov rv-split-prov-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 6, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 3, 'TcpExtTCPTimeouts': 3, 'TcpOutSegs': 20703695, 'TcpInSegs': 18244025}
- health synthprov rv-split-prov-2: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 3, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 3, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 10338305, 'TcpInSegs': 9098560}
