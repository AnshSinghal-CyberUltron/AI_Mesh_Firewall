# Harness analysis (sut, policy=none) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| error_rate_le_budget | FAIL |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 60000 over 300 s = 200.0 req/s (configured 200.0)
- qualified: 56260 (187.53 req/s); expected blocks: 0; errors: 3740 (rate 0.06233333333333333)
- error reasons: {'incomplete': 3740, 'unjoined': 3740, 'http_503': 2757, 'disposition_missing': 2757, 'stage_canon_missing': 2757, 'stage_det_missing': 2757, 'stage_sem_missing': 2757, 'stage_resolve_missing': 2757, 'stage_dispatch_missing': 2757, 'stage_out_missing': 2757, 'stage_audit_missing': 2757, 'http_403': 983, 'disposition_BLOCK': 983, 'stage_dispatch_S': 983, 'stage_out_S': 983}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=60000 p50=0.0837 p90=0.093 p99=0.1044 p99.9=0.1218 max=0.2755 mean=0.0835
- join: {'joined': 56260, 'by_nonce_fallback': 0, 'provider_records': 70412, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=56260 p50=10.5564 p90=14.7608 p99=20.0285 p99.9=26.0776 max=37.9057 mean=10.5269 |
| T_addon_first | n=56260 p50=10.5168 p90=14.9372 p99=27.2919 p99.9=35.6398 max=70.1037 mean=10.6999 |
| T_release_lag_max | n=5616 p50=47.6406 p90=55.4226 p99=72.2432 p99.9=85.605 max=93.1549 mean=38.6313 |
| T_release_lag_max_arrival | n=5616 p50=35.354 p90=41.9086 p99=47.0171 p99.9=53.0768 max=59.0579 mean=29.5846 |
| T_fw_addon | n=56260 p50=10.9768 p90=18.6705 p99=55.4404 p99.9=72.2432 max=93.1549 mean=13.6857 |
| T_fw_addon_sse | n=39397 p50=11.1407 p90=33.6015 p99=57.0919 p99.9=73.079 max=93.1549 mean=14.995 |
| T_fw_addon_json | n=16863 p50=10.6356 p90=14.8806 p99=20.5296 p99.9=26.1828 max=34.0405 mean=10.627 |
| client_ttft_sse | n=39397 p50=160.5021 p90=164.9981 p99=180.0508 p99.9=186.9256 max=220.1554 mean=160.7721 |
| provider_sched_err_last | n=56260 p50=0.0343 p90=0.0868 p99=0.1158 p99.9=0.1428 max=0.1934 mean=0.0409 |
| provider_sched_err_max_per_stream | n=56260 p50=0.1043 p90=0.1451 p99=0.1841 p99.9=0.2105 max=0.2463 mean=0.0948 |
| provider_write_max | n=56260 p50=0.0182 p90=0.0255 p99=0.0354 p99.9=0.0481 max=0.2418 mean=0.0182 |

- release lag: 5616 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22-200/rv-pbu-lg-1/lg', 'samples': 300, 'busy_max': 10.387631864988855, 'busy_mean': 7.4, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22-200/rv-pbu-lg-2/lg', 'samples': 300, 'busy_max': 11.09695968694342, 'busy_mean': 7.16, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 16.823740021941624, 'busy_p95': 12.873058647531522}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22-200/rv-pbu-lg-1/lg', 'scheduled': 37500, 'recorded': 37500, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22-200/rv-pbu-lg-2/lg', 'scheduled': 37500, 'recorded': 37500, 'interrupted': False}]
- health olg rv-pbu-lg-1: gc_cycles=0 sched_latency_max_ms=0.057344 tcp={'TcpRetransSegs': 31, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 31, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 3417225, 'TcpInSegs': 5482188}
- health olg rv-pbu-lg-2: gc_cycles=0 sched_latency_max_ms=0.13107200000000002 tcp={'TcpRetransSegs': 40, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 40, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 3470731, 'TcpInSegs': 5572855}
- health synthprov rv-pbu-prov-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 119, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 114, 'TcpExtTCPTimeouts': 6, 'TcpOutSegs': 11343612, 'TcpInSegs': 7733302}
