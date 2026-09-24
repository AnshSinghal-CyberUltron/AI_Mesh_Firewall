# Harness analysis (sut, policy=none) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| error_rate_le_budget | FAIL |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 30000 over 300 s = 100.0 req/s (configured 100.0)
- qualified: 29507 (98.36 req/s); expected blocks: 0; errors: 493 (rate 0.016433333333333335)
- error reasons: {'http_403': 493, 'incomplete': 493, 'unjoined': 493, 'disposition_BLOCK': 493, 'stage_dispatch_S': 493, 'stage_out_S': 493}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=30000 p50=0.0848 p90=0.0985 p99=0.1155 p99.9=0.1359 max=0.3092 mean=0.0804
- join: {'joined': 29507, 'by_nonce_fallback': 0, 'provider_records': 36905, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=29507 p50=9.6389 p90=12.9753 p99=16.1661 p99.9=20.5695 max=28.77 mean=9.225 |
| T_addon_first | n=29507 p50=9.5795 p90=13.1527 p99=25.9154 p99.9=32.7284 max=50.5674 mean=9.3891 |
| T_release_lag_max | n=2965 p50=46.3196 p90=53.6322 p99=71.2748 p99.9=75.2629 max=93.3933 mean=37.1608 |
| T_release_lag_max_arrival | n=2965 p50=32.1487 p90=37.8097 p99=42.5452 p99.9=45.5995 max=48.7643 mean=26.7011 |
| T_fw_addon | n=29507 p50=9.9112 p90=15.5489 p99=53.6506 p99.9=71.2748 max=93.3933 mean=12.311 |
| T_fw_addon_sse | n=20678 p50=10.0135 p90=31.1408 p99=55.0097 p99.9=72.5059 max=93.3933 mean=13.5995 |
| T_fw_addon_json | n=8829 p50=9.6842 p90=13.0794 p99=16.324 p99.9=20.8531 max=27.4589 mean=9.2932 |
| client_ttft_sse | n=20678 p50=159.5906 p90=163.237 p99=177.5706 p99.9=183.6688 max=200.6094 mean=159.4738 |
| provider_sched_err_last | n=29507 p50=0.0398 p90=0.0877 p99=0.1136 p99.9=0.1358 max=0.1792 mean=0.0437 |
| provider_sched_err_max_per_stream | n=29507 p50=0.0935 p90=0.1356 p99=0.1759 p99.9=0.2137 max=1.2106 mean=0.0891 |
| provider_write_max | n=29507 p50=0.0178 p90=0.0253 p99=0.0353 p99.9=0.0485 max=0.1071 mean=0.0177 |

- release lag: 2965 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22u-100/rv-pbu-lg-1/lg', 'samples': 300, 'busy_max': 8.337166848871359, 'busy_mean': 5.29, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22u-100/rv-pbu-lg-2/lg', 'samples': 300, 'busy_max': 5.702695615119424, 'busy_mean': 5.15, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 17.80074937309003, 'busy_p95': 8.885384426631514}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22u-100/rv-pbu-lg-1/lg', 'scheduled': 18750, 'recorded': 18750, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22u-100/rv-pbu-lg-2/lg', 'scheduled': 18750, 'recorded': 18750, 'interrupted': False}]
- health olg rv-pbu-lg-1: gc_cycles=0 sched_latency_max_ms=0.16384 tcp={'TcpRetransSegs': 9, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 4, 'TcpExtTCPTimeouts': 5, 'TcpOutSegs': 1812169, 'TcpInSegs': 2900389}
- health olg rv-pbu-lg-2: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 4, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 4, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1818085, 'TcpInSegs': 2899269}
- health synthprov rv-pbu-prov-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 23, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 23, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 5952421, 'TcpInSegs': 4185070}
