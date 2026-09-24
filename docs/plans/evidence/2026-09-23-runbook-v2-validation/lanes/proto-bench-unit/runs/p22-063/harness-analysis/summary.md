# Harness analysis (sut, policy=none) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| error_rate_le_budget | FAIL |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 18900 over 300 s = 63.0 req/s (configured 63.0)
- qualified: 18580 (61.93 req/s); expected blocks: 0; errors: 320 (rate 0.016931216931216932)
- error reasons: {'incomplete': 320, 'unjoined': 320, 'http_403': 318, 'disposition_BLOCK': 318, 'stage_dispatch_S': 318, 'stage_out_S': 318, 'http_503': 2, 'disposition_missing': 2, 'stage_canon_missing': 2, 'stage_det_missing': 2, 'stage_sem_missing': 2, 'stage_resolve_missing': 2, 'stage_dispatch_missing': 2, 'stage_out_missing': 2, 'stage_audit_missing': 2}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=18900 p50=0.0844 p90=0.0941 p99=0.106 p99.9=0.123 max=0.236 mean=0.0842
- join: {'joined': 18580, 'by_nonce_fallback': 0, 'provider_records': 23240, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=18580 p50=9.509 p90=12.3159 p99=15.2284 p99.9=20.4206 max=32.0209 mean=8.9456 |
| T_addon_first | n=18580 p50=9.4707 p90=12.9227 p99=25.3287 p99.9=33.4926 max=53.2305 mean=9.1337 |
| T_release_lag_max | n=1777 p50=46.2538 p90=52.5997 p99=70.5342 p99.9=85.0786 max=86.5895 mean=37.2336 |
| T_release_lag_max_arrival | n=1777 p50=32.0047 p90=37.5481 p99=40.8411 p99.9=47.0005 max=50.6831 mean=26.6726 |
| T_fw_addon | n=18580 p50=9.757 p90=14.3461 p99=52.2109 p99.9=70.4133 max=86.5895 mean=11.9335 |
| T_fw_addon_sse | n=13000 p50=9.8354 p90=29.8179 p99=53.4871 p99.9=71.2693 max=86.5895 mean=13.1688 |
| T_fw_addon_json | n=5580 p50=9.5977 p90=12.7667 p99=15.5194 p99.9=21.2232 max=32.0209 mean=9.0557 |
| client_ttft_sse | n=13000 p50=159.4584 p90=163.0115 p99=176.8778 p99.9=183.9854 max=203.3139 mean=159.2154 |
| provider_sched_err_last | n=18580 p50=0.0456 p90=0.0879 p99=0.1087 p99.9=0.125 max=0.1741 mean=0.0475 |
| provider_sched_err_max_per_stream | n=18580 p50=0.0813 p90=0.1222 p99=0.1485 p99.9=0.1866 max=0.2104 mean=0.0795 |
| provider_write_max | n=18580 p50=0.0158 p90=0.0237 p99=0.0333 p99.9=0.0457 max=0.1209 mean=0.0161 |

- release lag: 1777 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22-063/rv-pbu-lg-1/lg', 'samples': 300, 'busy_max': 7.955321685840788, 'busy_mean': 4.74, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22-063/rv-pbu-lg-2/lg', 'samples': 300, 'busy_max': 5.897554143158801, 'busy_mean': 4.52, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 18.577405985369833, 'busy_p95': 7.380026259506489}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22-063/rv-pbu-lg-1/lg', 'scheduled': 11813, 'recorded': 11813, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22-063/rv-pbu-lg-2/lg', 'scheduled': 11812, 'recorded': 11812, 'interrupted': False}]
- health olg rv-pbu-lg-1: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 11, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 6, 'TcpExtTCPTimeouts': 6, 'TcpOutSegs': 1180671, 'TcpInSegs': 1818735}
- health olg rv-pbu-lg-2: gc_cycles=0 sched_latency_max_ms=0.08192 tcp={'TcpRetransSegs': 2, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 2, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1175924, 'TcpInSegs': 1819132}
- health synthprov rv-pbu-prov-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 13, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 13, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 3733616, 'TcpInSegs': 2663904}
