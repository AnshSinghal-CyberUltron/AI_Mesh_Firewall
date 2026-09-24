# Harness analysis (sut, policy=none) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| error_rate_le_budget | FAIL |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 30299 over 300 s = 101.0 req/s (configured 100.0)
- qualified: 29788 (99.29 req/s); expected blocks: 0; errors: 511 (rate 0.016865243077329284)
- error reasons: {'http_403': 511, 'incomplete': 511, 'unjoined': 511, 'disposition_BLOCK': 511, 'stage_dispatch_S': 511, 'stage_out_S': 511}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=30299 p50=0.083 p90=0.0933 p99=0.1054 p99.9=0.1308 max=0.336 mean=0.0827
- join: {'joined': 29788, 'by_nonce_fallback': 0, 'provider_records': 37222, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=29788 p50=10.1576 p90=14.0277 p99=18.8499 p99.9=24.121 max=32.0986 mean=9.9197 |
| T_addon_first | n=29788 p50=10.1093 p90=14.139 p99=26.2364 p99.9=33.9599 max=52.5505 mean=10.0695 |
| T_release_lag_max | n=3010 p50=46.8388 p90=54.3189 p99=71.5694 p99.9=75.8314 max=80.0453 mean=38.2879 |
| T_release_lag_max_arrival | n=3010 p50=33.5673 p90=39.8988 p99=45.1879 p99.9=52.1143 max=61.6993 mean=28.3149 |
| T_fw_addon | n=29788 p50=10.4451 p90=17.6991 p99=54.3669 p99.9=71.598 max=80.0453 mean=13.0615 |
| T_fw_addon_sse | n=20875 p50=10.5709 p90=33.7273 p99=56.6094 p99.9=72.8767 max=80.0453 mean=14.3788 |
| T_fw_addon_json | n=8913 p50=10.1943 p90=14.1252 p99=18.8374 p99.9=24.1482 max=27.6667 mean=9.9762 |
| client_ttft_sse | n=20875 p50=160.121 p90=164.195 p99=178.9834 p99.9=184.7515 max=202.5988 mean=160.1558 |
| provider_sched_err_last | n=29788 p50=0.0448 p90=0.0883 p99=0.1117 p99.9=0.1309 max=0.1956 mean=0.0464 |
| provider_sched_err_max_per_stream | n=29788 p50=0.0897 p90=0.1289 p99=0.1655 p99.9=0.1946 max=0.5111 mean=0.0856 |
| provider_write_max | n=29788 p50=0.0172 p90=0.023 p99=0.0327 p99.9=0.0431 max=0.1094 mean=0.0166 |

- release lag: 3010 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22up-100/rv-pbu-lg-1/lg', 'samples': 300, 'busy_max': 16.83800874523087, 'busy_mean': 5.77, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22up-100/rv-pbu-lg-2/lg', 'samples': 300, 'busy_max': 18.217063959331114, 'busy_mean': 5.49, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 21.294090614622295, 'busy_p95': 9.169510439179728}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22up-100/rv-pbu-lg-1/lg', 'scheduled': 18861, 'recorded': 18861, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22up-100/rv-pbu-lg-2/lg', 'scheduled': 18979, 'recorded': 18979, 'interrupted': False}]
- health olg rv-pbu-lg-1: gc_cycles=0 sched_latency_max_ms=0.06553600000000001 tcp={'TcpRetransSegs': 10, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 10, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1935805, 'TcpInSegs': 2915665}
- health olg rv-pbu-lg-2: gc_cycles=0 sched_latency_max_ms=0.08192 tcp={'TcpRetransSegs': 3, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 3, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1940377, 'TcpInSegs': 2931615}
- health synthprov rv-pbu-prov-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 24, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 24, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 6002184, 'TcpInSegs': 4467757}
