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
- qualified: 14760 (49.2 req/s); expected blocks: 0; errors: 240 (rate 0.016)
- error reasons: {'http_403': 240, 'incomplete': 240, 'unjoined': 240, 'disposition_BLOCK': 240, 'stage_dispatch_S': 240, 'stage_out_S': 240}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=15000 p50=0.0885 p90=0.1039 p99=0.1181 p99.9=0.1368 max=0.271 mean=0.0852
- join: {'joined': 14760, 'by_nonce_fallback': 0, 'provider_records': 18463, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=14760 p50=9.7514 p90=11.6908 p99=14.0871 p99.9=14.8693 max=18.7111 mean=9.078 |
| T_addon_first | n=14760 p50=9.6923 p90=11.8739 p99=26.0864 p99.9=31.2961 max=50.9704 mean=9.2418 |
| T_release_lag_max | n=14760 p50=46.4749 p90=51.5202 p99=70.6199 p99.9=74.0858 max=92.7113 mean=36.6621 |
| T_release_lag_max_arrival | n=14760 p50=27.1457 p90=31.5627 p99=34.0382 p99.9=35.3464 max=51.0013 mean=23.2425 |
| T_fw_addon | n=14760 p50=46.4749 p90=51.5202 p99=70.6199 p99.9=74.0858 max=92.7113 mean=36.6621 |
| T_fw_addon_sse | n=10331 p50=49.4238 p90=53.13 p99=70.9822 p99.9=74.9241 max=92.7113 mean=48.4648 |
| T_fw_addon_json | n=4429 p50=9.8059 p90=11.7646 p99=14.1069 p99.9=14.8048 max=14.9951 mean=9.1314 |
| client_ttft_sse | n=10331 p50=159.6896 p90=162.034 p99=177.5835 p99.9=182.8316 max=201.0121 mean=159.3332 |
| provider_sched_err_last | n=14760 p50=0.0404 p90=0.0888 p99=0.1182 p99.9=0.2203 max=0.3157 mean=0.0441 |
| provider_sched_err_max_per_stream | n=14760 p50=0.0998 p90=0.2189 p99=0.3437 p99.9=0.5092 max=0.7295 mean=0.1089 |
| provider_write_max | n=14760 p50=0.0188 p90=0.0272 p99=0.0369 p99.9=0.0478 max=0.1612 mean=0.0188 |

- release lag: 14760 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/li-050/rv-pbu-lg-3/lg', 'samples': 300, 'busy_max': 16.63263433211124, 'busy_mean': 4.6, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/li-050/rv-pbu-lg-4/lg', 'samples': 300, 'busy_max': 16.485101305072547, 'busy_mean': 4.57, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 27.36467725852797, 'busy_p95': 6.4779676316683465}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/li-050/rv-pbu-lg-3/lg', 'scheduled': 9375, 'recorded': 9375, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/li-050/rv-pbu-lg-4/lg', 'scheduled': 9375, 'recorded': 9375, 'interrupted': False}]
- health olg rv-pbu-lg-3: gc_cycles=0 sched_latency_max_ms=0.08192 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 948617, 'TcpInSegs': 1445540}
- health olg rv-pbu-lg-4: gc_cycles=0 sched_latency_max_ms=0.196608 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 948085, 'TcpInSegs': 1445136}
- health synthprov rv-pbu-prov-2: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 29, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 30, 'TcpOutSegs': 2967827, 'TcpInSegs': 2261331}
