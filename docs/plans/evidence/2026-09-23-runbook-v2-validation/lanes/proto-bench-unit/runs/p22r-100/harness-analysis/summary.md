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
- qualified: 29436 (98.12 req/s); expected blocks: 0; errors: 564 (rate 0.0188)
- error reasons: {'incomplete': 564, 'unjoined': 564, 'http_403': 515, 'disposition_BLOCK': 515, 'stage_dispatch_S': 515, 'stage_out_S': 515, 'http_503': 49, 'disposition_missing': 49, 'stage_canon_missing': 49, 'stage_det_missing': 49, 'stage_sem_missing': 49, 'stage_resolve_missing': 49, 'stage_dispatch_missing': 49, 'stage_out_missing': 49, 'stage_audit_missing': 49}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=30000 p50=0.0864 p90=0.0986 p99=0.1135 p99.9=0.1342 max=0.2885 mean=0.0853
- join: {'joined': 29436, 'by_nonce_fallback': 0, 'provider_records': 36823, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=29436 p50=9.6069 p90=12.9542 p99=16.2493 p99.9=20.2875 max=27.9529 mean=9.2226 |
| T_addon_first | n=29436 p50=9.5463 p90=13.1031 p99=25.295 p99.9=33.2456 max=68.2915 mean=9.3576 |
| T_release_lag_max | n=2931 p50=46.5523 p90=53.4752 p99=71.1277 p99.9=74.7931 max=86.7375 mean=37.7663 |
| T_release_lag_max_arrival | n=2931 p50=32.377 p90=37.8075 p99=42.1665 p99.9=46.5647 max=49.0137 mean=27.0575 |
| T_fw_addon | n=29436 p50=9.8906 p90=15.3966 p99=53.4752 p99.9=71.1277 max=86.7375 mean=12.3091 |
| T_fw_addon_sse | n=20625 p50=10.0005 p90=32.2264 p99=54.5951 p99.9=71.6667 max=86.7375 mean=13.5942 |
| T_fw_addon_json | n=8811 p50=9.6896 p90=13.1091 p99=16.2316 p99.9=19.4346 max=23.049 mean=9.3009 |
| client_ttft_sse | n=20625 p50=159.5499 p90=163.1476 p99=176.9032 p99.9=183.9836 max=218.3229 mean=159.426 |
| provider_sched_err_last | n=29436 p50=0.0401 p90=0.0875 p99=0.1132 p99.9=0.1364 max=0.1866 mean=0.0439 |
| provider_sched_err_max_per_stream | n=29436 p50=0.0942 p90=0.1358 p99=0.1757 p99.9=0.2062 max=0.3906 mean=0.0887 |
| provider_write_max | n=29436 p50=0.0177 p90=0.0242 p99=0.0339 p99.9=0.0445 max=0.068 mean=0.0173 |

- release lag: 2931 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22r-100/rv-pbu-lg-1/lg', 'samples': 300, 'busy_max': 18.955294874285002, 'busy_mean': 5.41, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22r-100/rv-pbu-lg-2/lg', 'samples': 300, 'busy_max': 16.634942717612166, 'busy_mean': 5.16, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 20.22069769087309, 'busy_p95': 8.859359414202107}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22r-100/rv-pbu-lg-1/lg', 'scheduled': 18750, 'recorded': 18750, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22r-100/rv-pbu-lg-2/lg', 'scheduled': 18750, 'recorded': 18750, 'interrupted': False}]
- health olg rv-pbu-lg-1: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 8, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 8, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1802379, 'TcpInSegs': 2898568}
- health olg rv-pbu-lg-2: gc_cycles=0 sched_latency_max_ms=0.229376 tcp={'TcpRetransSegs': 4, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 3, 'TcpExtTCPTimeouts': 1, 'TcpOutSegs': 1802620, 'TcpInSegs': 2885225}
- health synthprov rv-pbu-prov-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 27, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 27, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 5937563, 'TcpInSegs': 4217326}
