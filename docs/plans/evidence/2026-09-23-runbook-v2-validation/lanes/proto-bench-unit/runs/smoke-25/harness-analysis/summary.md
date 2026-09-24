# Harness analysis (sut, policy=none) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| error_rate_le_budget | FAIL |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 1500 over 60 s = 25.0 req/s (configured 25.0)
- qualified: 1479 (24.65 req/s); expected blocks: 0; errors: 21 (rate 0.014)
- error reasons: {'http_403': 21, 'incomplete': 21, 'unjoined': 21, 'disposition_BLOCK': 21, 'stage_dispatch_S': 21, 'stage_out_S': 21}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=1500 p50=0.0913 p90=0.1072 p99=0.1229 p99.9=0.1452 max=0.1537 mean=0.0919
- join: {'joined': 1479, 'by_nonce_fallback': 0, 'provider_records': 2090, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=1479 p50=9.254 p90=11.3415 p99=14.3885 p99.9=21.9658 max=24.5903 mean=8.6153 |
| T_addon_first | n=1479 p50=9.206 p90=12.2816 p99=25.7349 p99.9=32.9556 max=33.3338 mean=8.825 |
| T_release_lag_max | n=152 p50=45.9962 p90=50.9293 p99=68.9818 p99.9=73.5924 max=73.5924 mean=37.2834 |
| T_release_lag_max_arrival | n=152 p50=30.8527 p90=36.0735 p99=39.0508 p99.9=47.1998 max=47.1998 mean=26.3545 |
| T_fw_addon | n=1479 p50=9.4629 p90=14.2696 p99=50.9849 p99.9=68.9818 max=73.5924 mean=11.8057 |
| T_fw_addon_sse | n=1033 p50=9.5055 p90=34.9878 p99=52.7921 p99.9=68.9818 max=73.5924 mean=13.1614 |
| T_fw_addon_json | n=446 p50=9.312 p90=11.7037 p99=13.9325 p99.9=18.4923 max=18.4923 mean=8.6658 |
| client_ttft_sse | n=1033 p50=159.2292 p90=162.6587 p99=176.4214 p99.9=183.0158 max=183.4104 mean=158.9419 |
| provider_sched_err_last | n=1479 p50=0.0457 p90=0.0894 p99=0.1139 p99.9=0.1981 max=0.202 mean=0.0479 |
| provider_sched_err_max_per_stream | n=1479 p50=0.0927 p90=0.2415 p99=0.4394 p99.9=0.7212 max=0.8169 mean=0.1138 |
| provider_write_max | n=1479 p50=0.0175 p90=0.0253 p99=0.0362 p99.9=0.0511 max=0.0569 mean=0.0175 |

- release lag: 152 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/smoke-25/rv-pbu-lg-1/lg', 'samples': 60, 'busy_max': 6.961741159979773, 'busy_mean': 4.08, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/smoke-25/rv-pbu-lg-2/lg', 'samples': 60, 'busy_max': 4.25837791538849, 'busy_mean': 4.09, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 124, 'busy_max': 6.409573570399929, 'busy_p95': 5.175818710820412}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/smoke-25/rv-pbu-lg-1/lg', 'scheduled': 1063, 'recorded': 1063, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/smoke-25/rv-pbu-lg-2/lg', 'scheduled': 1062, 'recorded': 1062, 'interrupted': False}]
- health olg rv-pbu-lg-1: gc_cycles=0 sched_latency_max_ms=0.196608 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 129552, 'TcpInSegs': 166444}
- health olg rv-pbu-lg-2: gc_cycles=0 sched_latency_max_ms=0.32768 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 130927, 'TcpInSegs': 165860}
- health synthprov rv-pbu-prov-1: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 1, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 1, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 340839, 'TcpInSegs': 283508}
