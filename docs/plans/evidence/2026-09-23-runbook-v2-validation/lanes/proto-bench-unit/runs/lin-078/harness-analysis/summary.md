# Harness analysis (sut, policy=none) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | PASS |
| error_rate_le_budget | FAIL |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 23400 over 300 s = 78.0 req/s (configured 78.0)
- qualified: 22975 (76.58 req/s); expected blocks: 0; errors: 425 (rate 0.018162393162393164)
- error reasons: {'http_403': 425, 'incomplete': 425, 'unjoined': 425, 'disposition_BLOCK': 425, 'stage_dispatch_S': 425, 'stage_out_S': 425}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=23400 p50=0.0846 p90=0.0941 p99=0.1055 p99.9=0.132 max=0.3274 mean=0.0846
- join: {'joined': 22975, 'by_nonce_fallback': 0, 'provider_records': 28751, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=22975 p50=9.6892 p90=11.9345 p99=14.5103 p99.9=15.5119 max=22.6366 mean=9.0369 |
| T_addon_first | n=22975 p50=9.6275 p90=11.8877 p99=14.4484 p99.9=15.5119 max=22.5292 mean=8.98 |
| T_release_lag_max | n=22975 p50=9.7927 p90=12.1251 p99=14.7756 p99.9=19.7648 max=32.8996 mean=9.1809 |
| T_release_lag_max_arrival | n=22975 p50=9.7927 p90=12.1251 p99=14.7756 p99.9=19.7648 max=32.8996 mean=9.1809 |
| T_fw_addon | n=22975 p50=9.7927 p90=12.1251 p99=14.7756 p99.9=19.7648 max=32.8996 mean=9.1809 |
| T_fw_addon_sse | n=16081 p50=9.7972 p90=12.1576 p99=14.8558 p99.9=20.6699 max=32.8996 mean=9.1994 |
| T_fw_addon_json | n=6894 p50=9.7841 p90=12.0679 p99=14.6244 p99.9=15.8745 max=16.6181 mean=9.1379 |
| client_ttft_sse | n=16081 p50=159.6214 p90=161.8421 p99=164.4073 p99.9=165.3621 max=172.6159 mean=158.9597 |
| provider_sched_err_last | n=22975 p50=0.046 p90=0.0877 p99=0.1097 p99.9=0.1252 max=0.2635 mean=0.0475 |
| provider_sched_err_max_per_stream | n=22975 p50=0.0833 p90=0.1236 p99=0.1554 p99.9=0.1941 max=0.3324 mean=0.0811 |
| provider_write_max | n=22975 p50=0.0167 p90=0.0229 p99=0.031 p99.9=0.0403 max=0.1032 mean=0.0162 |

- release lag: 22975 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/lin-078/rv-pbu-lg-3/lg', 'samples': 300, 'busy_max': 8.001403616512015, 'busy_mean': 4.93, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/lin-078/rv-pbu-lg-4/lg', 'samples': 300, 'busy_max': 5.392849866189675, 'busy_mean': 4.97, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 11.332735281319117, 'busy_p95': 7.713044737832608}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/lin-078/rv-pbu-lg-3/lg', 'scheduled': 14625, 'recorded': 14625, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/lin-078/rv-pbu-lg-4/lg', 'scheduled': 14625, 'recorded': 14625, 'interrupted': False}]
- health olg rv-pbu-lg-3: gc_cycles=0 sched_latency_max_ms=0.16384 tcp={'TcpRetransSegs': 2, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1400975, 'TcpInSegs': 2293385}
- health olg rv-pbu-lg-4: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 5, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 6, 'TcpOutSegs': 1399792, 'TcpInSegs': 2292682}
- health synthprov rv-pbu-prov-2: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 2, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 2, 'TcpExtTCPTimeouts': 2, 'TcpOutSegs': 4629542, 'TcpInSegs': 3280614}
