# Harness analysis (sut, policy={'blocks_total': 2369, 'expected_blocks': 0, 'false_positive_blocks': 2369, 'other_blocks': 0, 'benign_offered': 139800, 'false_positive_rate': 0.016945636623748213, 'policy_misses': 0, 'latency_client_total_ms': {'policy_block_fp': {'n': 2369, 'min': 3.3817, 'p50': 7.5791, 'p90': 10.1146, 'p99': 15.7761, 'p999': 22.1626, 'max': 24.2421, 'mean': 7.8116}}, 'block_rule': {'statuses': [400, 403, 422, 451], 'envelope_re': '(?i)content_filter|content_polic|polic(y|ies)_|blocked|block_|guard|violation|unsafe|threat'}}) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| infra_error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 139800 over 300 s = 466.0 req/s (configured 466.0)
- qualified: 137431 (458.1 req/s; dispositions ['ALLOW', 'FLAG', 'REDACT'], source auto)
- infra errors (the error-budget gate): 0 (rate 0.0); reasons: {}
- policy stratum (not qualified, not infra errors): expected blocks 0, FALSE-POSITIVE blocks 2369 of 139800 benign (FP rate 0.016945636623748213), other blocks 0, policy misses 0
- policy cohort latency policy_block_fp (client total ms): n=2369 p50=7.5791 p90=10.1146 p99=15.7761 p99.9=22.1626 max=24.2421 mean=7.8116
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=139800 p50=0.0852 p90=0.0967 p99=0.1108 p99.9=0.131 max=0.5963 mean=0.0848
- join: {'joined': 137431, 'by_nonce_fallback': 0, 'provider_records': 171795, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=137431 p50=8.0453 p90=11.0488 p99=15.5492 p99.9=19.922 max=34.2227 mean=7.8832 |
| T_addon_first | n=137431 p50=8.0393 p90=11.1315 p99=24.8758 p99.9=31.4275 max=50.8914 mean=8.0978 |
| T_release_lag_max | n=137431 p50=45.2761 p90=50.9256 p99=69.0441 p99.9=75.6927 max=110.2188 mean=35.5888 |
| T_release_lag_max_arrival | n=137431 p50=25.796 p90=30.8099 p99=35.5307 p99.9=42.2107 max=68.4455 mean=22.0538 |
| T_fw_addon | n=137431 p50=45.2761 p90=50.9256 p99=69.0441 p99.9=75.6927 max=110.2188 mean=35.5888 |
| T_fw_addon_sse | n=96235 p50=47.8782 p90=52.4121 p99=70.2959 p99.9=84.0635 max=110.2188 mean=47.4175 |
| T_fw_addon_json | n=41196 p50=8.1143 p90=11.1167 p99=15.5974 p99.9=19.9835 max=34.2227 mean=7.9566 |
| client_ttft_sse | n=96235 p50=158.0487 p90=161.1859 p99=177.3889 p99.9=182.9526 max=200.9475 mean=158.1986 |
| provider_sched_err_last | n=137431 p50=0.0331 p90=0.086 p99=0.118 p99.9=0.1489 max=0.2857 mean=0.0402 |
| provider_sched_err_max_per_stream | n=137431 p50=0.1097 p90=0.1532 p99=0.1945 p99.9=0.2267 max=0.5524 mean=0.0993 |
| provider_write_max | n=137431 p50=0.0209 p90=0.0286 p99=0.0396 p99.9=0.0507 max=0.1858 mean=0.0205 |

- release lag: 137431 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/li34-466/rv-split-lg-1/lg', 'samples': 300, 'busy_max': 23.86465132666703, 'busy_mean': 10.46, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-b'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/li34-466/rv-split-lg-2/lg', 'samples': 300, 'busy_max': 28.307437800304292, 'busy_mean': 15.88, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-b'}]
- provider CPU: {'samples': 868, 'busy_max': 30.275409593973237, 'busy_p95': 18.09125958159308}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/li34-466/rv-split-lg-1/lg', 'scheduled': 87375, 'recorded': 87375, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/li34-466/rv-split-lg-2/lg', 'scheduled': 87375, 'recorded': 87375, 'interrupted': False}]
- health olg rv-split-lg-1: gc_cycles=0 sched_latency_max_ms=0.16384 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 7708193, 'TcpInSegs': 13645736}
- health olg rv-split-lg-2: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 22, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 6, 'TcpExtTCPTimeouts': 15, 'TcpOutSegs': 7687076, 'TcpInSegs': 13630044}
- health synthprov rv-split-prov-1: gc_cycles=0 sched_latency_max_ms=0.13107200000000002 tcp={'TcpRetransSegs': 3, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 3, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 18565424, 'TcpInSegs': 16500742}
- health synthprov rv-split-prov-2: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 1, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 1, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 9268699, 'TcpInSegs': 8215039}
