# Harness analysis (sut, policy=none) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| error_rate_le_budget | FAIL |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 45542 over 300 s = 151.81 req/s (configured 150.0)
- qualified: 44730 (149.1 req/s); expected blocks: 0; errors: 812 (rate 0.0178296956655395)
- error reasons: {'http_403': 812, 'incomplete': 812, 'unjoined': 812, 'disposition_BLOCK': 812, 'stage_dispatch_S': 812, 'stage_out_S': 812, 'stage_sem_U': 2}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=45542 p50=0.0827 p90=0.0927 p99=0.1041 p99.9=0.1291 max=0.2816 mean=0.082
- join: {'joined': 44730, 'by_nonce_fallback': 0, 'provider_records': 55831, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=44730 p50=10.7612 p90=15.348 p99=21.4502 p99.9=27.6571 max=59.8531 mean=10.7809 |
| T_addon_first | n=44730 p50=10.7073 p90=15.5439 p99=27.0758 p99.9=37.5588 max=66.3766 mean=10.9169 |
| T_release_lag_max | n=4475 p50=47.4519 p90=55.8784 p99=73.1578 p99.9=83.823 max=92.9558 mean=39.256 |
| T_release_lag_max_arrival | n=4475 p50=35.2696 p90=42.2292 p99=49.2658 p99.9=65.7768 max=70.5778 mean=30.0449 |
| T_fw_addon | n=44730 p50=11.1144 p90=19.7392 p99=55.8997 p99.9=73.1578 max=92.9558 mean=13.9144 |
| T_fw_addon_sse | n=31312 p50=11.2501 p90=34.8797 p99=57.9272 p99.9=74.2259 max=92.9558 mean=15.2151 |
| T_fw_addon_json | n=13418 p50=10.8397 p90=15.4102 p99=21.5708 p99.9=28.1994 max=54.3963 mean=10.8791 |
| client_ttft_sse | n=31312 p50=160.6987 p90=165.6427 p99=179.8493 p99.9=188.1335 max=216.4335 mean=160.9761 |
| provider_sched_err_last | n=44730 p50=0.0385 p90=0.0866 p99=0.113 p99.9=0.1344 max=0.1787 mean=0.0428 |
| provider_sched_err_max_per_stream | n=44730 p50=0.0954 p90=0.1361 p99=0.1731 p99.9=0.2046 max=0.2886 mean=0.0895 |
| provider_write_max | n=44730 p50=0.0178 p90=0.0254 p99=0.0351 p99.9=0.0467 max=0.092 mean=0.0178 |

- release lag: 4475 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22up-150/rv-pbu-lg-1/lg', 'samples': 300, 'busy_max': 10.256735968629837, 'busy_mean': 6.95, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22up-150/rv-pbu-lg-2/lg', 'samples': 300, 'busy_max': 7.338938244248483, 'busy_mean': 6.64, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 12.148761898319126, 'busy_p95': 11.685125325113145}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22up-150/rv-pbu-lg-1/lg', 'scheduled': 28409, 'recorded': 28409, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22up-150/rv-pbu-lg-2/lg', 'scheduled': 28394, 'recorded': 28394, 'interrupted': False}]
- health olg rv-pbu-lg-1: gc_cycles=0 sched_latency_max_ms=0.08192 tcp={'TcpRetransSegs': 28, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 28, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 2844749, 'TcpInSegs': 4373035}
- health olg rv-pbu-lg-2: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 14, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 14, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 2835980, 'TcpInSegs': 4373440}
- health synthprov rv-pbu-prov-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 109, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 109, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 8979747, 'TcpInSegs': 6538662}
