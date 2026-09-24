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
- qualified: 29470 (98.23 req/s); expected blocks: 0; errors: 530 (rate 0.017666666666666667)
- error reasons: {'http_403': 530, 'incomplete': 530, 'unjoined': 530, 'disposition_BLOCK': 530, 'stage_dispatch_S': 530, 'stage_out_S': 530}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=30000 p50=0.0878 p90=0.1003 p99=0.1177 p99.9=0.14 max=0.3079 mean=0.0888
- join: {'joined': 29470, 'by_nonce_fallback': 0, 'provider_records': 36863, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=29470 p50=9.8575 p90=12.4238 p99=14.7722 p99.9=15.9724 max=22.4729 mean=9.3418 |
| T_addon_first | n=29470 p50=9.8206 p90=12.642 p99=26.4009 p99.9=32.7801 max=53.5185 mean=9.5202 |
| T_release_lag_max | n=29470 p50=46.6631 p90=52.2626 p99=71.1746 p99.9=74.754 max=106.5382 mean=36.9834 |
| T_release_lag_max_arrival | n=29470 p50=27.3758 p90=32.1483 p99=34.8504 p99.9=42.332 max=42.9555 mean=23.5769 |
| T_fw_addon | n=29470 p50=46.6631 p90=52.2626 p99=71.1746 p99.9=74.754 max=106.5382 mean=36.9834 |
| T_fw_addon_sse | n=20650 p50=49.5293 p90=53.5218 p99=71.5925 p99.9=86.9705 max=106.5382 mean=48.7639 |
| T_fw_addon_json | n=8820 p50=9.8925 p90=12.5358 p99=14.8375 p99.9=16.1137 max=18.4893 mean=9.4021 |
| client_ttft_sse | n=20650 p50=159.8359 p90=162.7328 p99=179.3069 p99.9=183.5321 max=203.5894 mean=159.6148 |
| provider_sched_err_last | n=29470 p50=0.0406 p90=0.0872 p99=0.111 p99.9=0.1323 max=0.1832 mean=0.044 |
| provider_sched_err_max_per_stream | n=29470 p50=0.092 p90=0.1334 p99=0.1702 p99.9=0.2012 max=0.3076 mean=0.0871 |
| provider_write_max | n=29470 p50=0.0178 p90=0.0246 p99=0.0332 p99.9=0.0443 max=0.2112 mean=0.0174 |

- release lag: 29470 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/li-100/rv-pbu-lg-3/lg', 'samples': 300, 'busy_max': 8.690449298701408, 'busy_mean': 5.4, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/li-100/rv-pbu-lg-4/lg', 'samples': 300, 'busy_max': 5.81258361479402, 'busy_mean': 5.27, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 21.256877256723904, 'busy_p95': 8.676112692606331}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/li-100/rv-pbu-lg-3/lg', 'scheduled': 18750, 'recorded': 18750, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/li-100/rv-pbu-lg-4/lg', 'scheduled': 18750, 'recorded': 18750, 'interrupted': False}]
- health olg rv-pbu-lg-3: gc_cycles=0 sched_latency_max_ms=0.16384 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1775736, 'TcpInSegs': 2899140}
- health olg rv-pbu-lg-4: gc_cycles=0 sched_latency_max_ms=0.196608 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1791737, 'TcpInSegs': 2897130}
- health synthprov rv-pbu-prov-2: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 1, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 1, 'TcpOutSegs': 5947716, 'TcpInSegs': 4223131}
