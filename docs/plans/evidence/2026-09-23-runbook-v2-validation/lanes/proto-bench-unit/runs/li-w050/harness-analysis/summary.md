# Harness analysis (sut, policy=none) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| error_rate_le_budget | FAIL |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 15000 over 300 s = 50.0 req/s (configured 50.0)
- qualified: 14607 (48.69 req/s); expected blocks: 0; errors: 393 (rate 0.0262)
- error reasons: {'http_403': 393, 'incomplete': 393, 'unjoined': 393, 'disposition_BLOCK': 393, 'stage_dispatch_S': 393, 'stage_out_S': 393}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=15000 p50=0.0835 p90=0.0963 p99=0.1084 p99.9=0.149 max=0.3088 mean=0.0835
- join: {'joined': 14607, 'by_nonce_fallback': 0, 'provider_records': 18266, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=14607 p50=15.3539 p90=16.308 p99=16.9487 p99.9=17.3165 max=25.909 mean=15.3308 |
| T_addon_first | n=14607 p50=15.3006 p90=16.3376 p99=34.0353 p99.9=36.2351 max=55.8393 mean=15.5035 |
| T_release_lag_max | n=14607 p50=55.0004 p90=56.7035 p99=75.9206 p99.9=93.8384 max=96.0479 mean=44.7776 |
| T_release_lag_max_arrival | n=14607 p50=35.8962 p90=37.1373 p99=45.4075 p99.9=45.9276 max=55.016 mean=30.1577 |
| T_fw_addon | n=14607 p50=55.0004 p90=56.7035 p99=75.9206 p99.9=93.8384 max=96.0479 mean=44.7776 |
| T_fw_addon_sse | n=10228 p50=55.5545 p90=65.8435 p99=76.0891 p99.9=95.0108 max=96.0479 mean=57.3311 |
| T_fw_addon_json | n=4379 p50=15.4757 p90=16.3859 p99=16.9928 p99.9=17.318 max=25.1787 mean=15.4564 |
| client_ttft_sse | n=10228 p50=165.2679 p90=166.3483 p99=184.8311 p99.9=186.364 max=205.8406 mean=165.5651 |
| provider_sched_err_last | n=14607 p50=0.0257 p90=0.0996 p99=0.2096 p99.9=0.3433 max=0.639 mean=0.042 |
| provider_sched_err_max_per_stream | n=14607 p50=0.221 p90=0.3725 p99=0.5501 p99.9=0.7128 max=0.9072 mean=0.2078 |
| provider_write_max | n=14607 p50=0.0244 p90=0.0333 p99=0.0457 p99.9=0.0558 max=0.086 mean=0.0255 |

- release lag: 14607 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/li-w050/rv-pbu-lg-3/lg', 'samples': 300, 'busy_max': 16.51193182067714, 'busy_mean': 5.68, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/li-w050/rv-pbu-lg-4/lg', 'samples': 300, 'busy_max': 16.195095923187885, 'busy_mean': 5.63, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 22.490242589799912, 'busy_p95': 7.382084309385839}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/li-w050/rv-pbu-lg-3/lg', 'scheduled': 9375, 'recorded': 9375, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/li-w050/rv-pbu-lg-4/lg', 'scheduled': 9375, 'recorded': 9375, 'interrupted': False}]
- health olg rv-pbu-lg-3: gc_cycles=0 sched_latency_max_ms=0.16384 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1553514, 'TcpInSegs': 2531822}
- health olg rv-pbu-lg-4: gc_cycles=0 sched_latency_max_ms=0.08192 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1546747, 'TcpInSegs': 2530252}
- health synthprov rv-pbu-prov-2: gc_cycles=0 sched_latency_max_ms=0.16384 tcp={'TcpRetransSegs': 6, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 1, 'TcpExtTCPTimeouts': 7, 'TcpOutSegs': 5175620, 'TcpInSegs': 2907854}
