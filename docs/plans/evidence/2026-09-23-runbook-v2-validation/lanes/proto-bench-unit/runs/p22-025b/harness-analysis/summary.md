# Harness analysis (sut, policy=none) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| error_rate_le_budget | FAIL |
| zero_schedule_drops | FAIL |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 7500 over 300 s = 25.0 req/s (configured 25.0)
- qualified: 7398 (24.66 req/s); expected blocks: 0; errors: 102 (rate 0.0136)
- error reasons: {'http_403': 102, 'incomplete': 102, 'unjoined': 102, 'disposition_BLOCK': 102, 'stage_dispatch_S': 102, 'stage_out_S': 102}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 1; lateness ms: n=7500 p50=0.0917 p90=0.107 p99=0.1234 p99.9=0.1405 max=9.3147 mean=0.0943
- join: {'joined': 7398, 'by_nonce_fallback': 0, 'provider_records': 9243, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=7398 p50=9.556 p90=12.1468 p99=14.5423 p99.9=18.9991 max=22.4418 mean=8.9261 |
| T_addon_first | n=7398 p50=9.4988 p90=12.7691 p99=25.2578 p99.9=32.5147 max=50.3938 mean=9.0768 |
| T_release_lag_max | n=739 p50=46.1132 p90=52.9633 p99=70.4337 p99.9=73.1928 max=73.1928 mean=36.9416 |
| T_release_lag_max_arrival | n=739 p50=32.2497 p90=37.2849 p99=40.4077 p99.9=42.8331 max=42.8331 mean=26.5596 |
| T_fw_addon | n=7398 p50=9.8013 p90=14.1683 p99=52.9633 p99.9=70.4337 max=73.1928 mean=11.9484 |
| T_fw_addon_sse | n=5172 p50=9.8673 p90=30.6868 p99=54.004 p99.9=70.634 max=73.1928 mean=13.2351 |
| T_fw_addon_json | n=2226 p50=9.6306 p90=12.3758 p99=14.7834 p99.9=18.8687 max=19.3023 mean=8.9589 |
| client_ttft_sse | n=5172 p50=159.4842 p90=162.8482 p99=176.7112 p99.9=183.2152 max=200.3996 mean=159.1766 |
| provider_sched_err_last | n=7398 p50=0.0452 p90=0.0891 p99=0.1167 p99.9=0.2284 max=0.4869 mean=0.0478 |
| provider_sched_err_max_per_stream | n=7398 p50=0.0931 p90=0.2378 p99=0.3824 p99.9=0.5851 max=0.6585 mean=0.1107 |
| provider_write_max | n=7398 p50=0.0179 p90=0.0261 p99=0.0388 p99.9=0.0635 max=0.1169 mean=0.0181 |

- release lag: 739 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22-025b/rv-pbu-lg-1/lg', 'samples': 300, 'busy_max': 17.703214677600176, 'busy_mean': 4.14, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22-025b/rv-pbu-lg-2/lg', 'samples': 300, 'busy_max': 15.601366996879895, 'busy_mean': 4.2, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 17.556693788630973, 'busy_p95': 5.551718017180174}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22-025b/rv-pbu-lg-1/lg', 'scheduled': 4688, 'recorded': 4688, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22-025b/rv-pbu-lg-2/lg', 'scheduled': 4687, 'recorded': 4687, 'interrupted': False}]
- health olg rv-pbu-lg-1: gc_cycles=0 sched_latency_max_ms=0.06553600000000001 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 551120, 'TcpInSegs': 729736}
- health olg rv-pbu-lg-2: gc_cycles=0 sched_latency_max_ms=0.16384 tcp={'TcpRetransSegs': 8, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 1, 'TcpExtTCPTimeouts': 8, 'TcpOutSegs': 552591, 'TcpInSegs': 728375}
- health synthprov rv-pbu-prov-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 1, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 1, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1495962, 'TcpInSegs': 1233666}
