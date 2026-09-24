# Harness analysis (sut, policy=none) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| error_rate_le_budget | FAIL |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 7500 over 300 s = 25.0 req/s (configured 25.0)
- qualified: 7333 (24.44 req/s); expected blocks: 0; errors: 167 (rate 0.022266666666666667)
- error reasons: {'http_403': 167, 'incomplete': 167, 'unjoined': 167, 'disposition_BLOCK': 167, 'stage_dispatch_S': 167, 'stage_out_S': 167}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=7500 p50=0.0884 p90=0.1002 p99=0.1151 p99.9=0.1475 max=0.3379 mean=0.0895
- join: {'joined': 7333, 'by_nonce_fallback': 0, 'provider_records': 9161, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=7333 p50=14.3303 p90=14.9295 p99=19.041 p99.9=21.7998 max=22.5239 mean=14.3816 |
| T_addon_first | n=7333 p50=14.2682 p90=15.0133 p99=33.6042 p99.9=34.6652 max=73.8781 mean=14.6178 |
| T_release_lag_max | n=723 p50=54.0718 p90=56.9144 p99=74.5446 p99.9=97.3509 max=97.3509 mean=43.8935 |
| T_release_lag_max_arrival | n=723 p50=40.6514 p90=41.9625 p99=45.2882 p99.9=48.4249 max=48.4249 mean=33.1055 |
| T_fw_addon | n=7333 p50=14.468 p90=17.2203 p99=56.9144 p99.9=74.5446 max=97.3509 mean=17.6549 |
| T_fw_addon_sse | n=5132 p50=14.4582 p90=34.5735 p99=61.9868 p99.9=74.6985 max=97.3509 mean=18.9937 |
| T_fw_addon_json | n=2201 p50=14.4798 p90=15.0334 p99=19.9785 p99.9=21.7361 max=22.5239 mean=14.5332 |
| client_ttft_sse | n=5132 p50=164.2352 p90=165.0083 p99=184.0068 p99.9=184.8238 max=223.8891 mean=164.7011 |
| provider_sched_err_last | n=7333 p50=0.0321 p90=0.0964 p99=0.2232 p99.9=0.3564 max=0.9024 mean=0.0446 |
| provider_sched_err_max_per_stream | n=7333 p50=0.2809 p90=0.4515 p99=0.7523 p99.9=1.0332 max=1.1596 mean=0.2501 |
| provider_write_max | n=7333 p50=0.0244 p90=0.0346 p99=0.0515 p99.9=0.0672 max=0.1266 mean=0.0258 |

- release lag: 723 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/w22-025-r3/rv-pbu-lg-1/lg', 'samples': 300, 'busy_max': 18.085337523854673, 'busy_mean': 4.69, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/w22-025-r3/rv-pbu-lg-2/lg', 'samples': 300, 'busy_max': 18.07331372744073, 'busy_mean': 4.81, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 17.344224586410284, 'busy_p95': 5.857787176190154}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/w22-025-r3/rv-pbu-lg-1/lg', 'scheduled': 4688, 'recorded': 4688, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/w22-025-r3/rv-pbu-lg-2/lg', 'scheduled': 4687, 'recorded': 4687, 'interrupted': False}]
- health olg rv-pbu-lg-1: gc_cycles=0 sched_latency_max_ms=0.08192 tcp={'TcpRetransSegs': 8, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 6, 'TcpOutSegs': 776019, 'TcpInSegs': 1270025}
- health olg rv-pbu-lg-2: gc_cycles=0 sched_latency_max_ms=0.16384 tcp={'TcpRetransSegs': 1, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 1, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 768224, 'TcpInSegs': 1267580}
- health synthprov rv-pbu-prov-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 9, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 3, 'TcpExtTCPTimeouts': 7, 'TcpOutSegs': 2593806, 'TcpInSegs': 1502893}
