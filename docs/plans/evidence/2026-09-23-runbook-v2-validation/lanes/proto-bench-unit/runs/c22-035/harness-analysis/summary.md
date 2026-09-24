# Harness analysis (sut, policy=none) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| error_rate_le_budget | FAIL |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 21000 over 600 s = 35.0 req/s (configured 35.0)
- qualified: 15081 (25.14 req/s); expected blocks: 0; errors: 5919 (rate 0.28185714285714286)
- error reasons: {'content_mismatch': 4319, 'http_403': 1600, 'incomplete': 1600, 'unjoined': 1600, 'disposition_BLOCK': 1600, 'stage_dispatch_S': 1600, 'stage_out_S': 1600}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=21000 p50=0.0834 p90=0.0956 p99=0.1107 p99.9=0.1357 max=0.3421 mean=0.0841
- join: {'joined': 19400, 'by_nonce_fallback': 0, 'provider_records': 21840, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=15081 p50=6.1132 p90=6.944 p99=11.5927 p99.9=13.8956 max=18.575 mean=6.2055 |
| T_addon_first | n=15081 p50=6.0511 p90=6.9235 p99=25.0082 p99.9=26.8194 max=45.739 mean=6.3633 |
| T_release_lag_max | n=1471 p50=45.0752 p90=46.5994 p99=65.8143 p99.9=71.4497 max=72.998 mean=32.0213 |
| T_release_lag_max_arrival | n=1471 p50=27.4667 p90=32.65 p99=34.456 p99.9=38.3211 max=38.7918 mean=22.4701 |
| T_fw_addon | n=15081 p50=6.1972 p90=9.0278 p99=46.5856 p99.9=65.7756 max=72.998 mean=8.951 |
| T_fw_addon_sse | n=10571 p50=6.2125 p90=25.8604 p99=46.8162 p99.9=65.9633 max=72.998 mean=10.1037 |
| T_fw_addon_json | n=4510 p50=6.1628 p90=6.9819 p99=11.7331 p99.9=13.9368 max=14.4272 mean=6.2494 |
| client_ttft_sse | n=10571 p50=156.0506 p90=156.9432 p99=175.5761 p99.9=177.1557 max=195.764 mean=156.4643 |
| provider_sched_err_last | n=19400 p50=0.0528 p90=0.0879 p99=0.105 p99.9=0.1183 max=0.1346 mean=0.0524 |
| provider_sched_err_max_per_stream | n=19400 p50=0.0742 p90=0.1105 p99=0.1279 p99.9=0.1599 max=0.2081 mean=0.0718 |
| provider_write_max | n=19400 p50=0.0104 p90=0.0203 p99=0.029 p99.9=0.0365 max=0.0756 mean=0.0125 |

- release lag: 1471 sampled streams joined, 458 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/c22-035/rv-pbu-lg-1/lg', 'samples': 600, 'busy_max': 17.013497210684967, 'busy_mean': 3.8, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/c22-035/rv-pbu-lg-2/lg', 'samples': 600, 'busy_max': 16.201290101316744, 'busy_mean': 3.62, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 726, 'busy_max': 17.569746161567735, 'busy_p95': 4.998024244853461}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/c22-035/rv-pbu-lg-1/lg', 'scheduled': 11813, 'recorded': 11813, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/c22-035/rv-pbu-lg-2/lg', 'scheduled': 11812, 'recorded': 11812, 'interrupted': False}]
- health olg rv-pbu-lg-1: gc_cycles=0 sched_latency_max_ms=0.16384 tcp={'TcpRetransSegs': 10, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 10, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 687636, 'TcpInSegs': 940499}
- health olg rv-pbu-lg-2: gc_cycles=0 sched_latency_max_ms=0.049152 tcp={'TcpRetransSegs': 12, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 12, 'TcpExtTCPTimeouts': 1, 'TcpOutSegs': 690279, 'TcpInSegs': 939577}
- health synthprov rv-pbu-prov-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 13, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 13, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1935069, 'TcpInSegs': 1536895}
