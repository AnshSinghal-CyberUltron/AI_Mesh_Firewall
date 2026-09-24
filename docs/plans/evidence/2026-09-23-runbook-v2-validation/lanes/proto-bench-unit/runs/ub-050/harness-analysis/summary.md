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
- qualified: 14755 (49.18 req/s); expected blocks: 0; errors: 245 (rate 0.01633333333333333)
- error reasons: {'http_403': 245, 'incomplete': 245, 'unjoined': 245, 'disposition_BLOCK': 245, 'stage_dispatch_S': 245, 'stage_out_S': 245}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=15000 p50=0.0903 p90=0.1035 p99=0.1185 p99.9=0.148 max=3.0287 mean=0.0906
- join: {'joined': 14755, 'by_nonce_fallback': 0, 'provider_records': 18457, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=14755 p50=9.5958 p90=12.4227 p99=15.2983 p99.9=19.1782 max=22.4684 mean=9.0282 |
| T_addon_first | n=14755 p50=9.5388 p90=12.7701 p99=25.2599 p99.9=32.7923 max=53.1066 mean=9.1682 |
| T_release_lag_max | n=14755 p50=46.2877 p90=52.925 p99=70.4559 p99.9=76.0357 max=92.7576 mean=36.8453 |
| T_release_lag_max_arrival | n=14755 p50=31.3708 p90=36.8601 p99=39.921 p99.9=42.4697 max=49.0951 mean=26.1206 |
| T_fw_addon | n=14755 p50=46.2877 p90=52.925 p99=70.4559 p99.9=76.0357 max=92.7576 mean=36.8453 |
| T_fw_addon_sse | n=10325 p50=49.2884 p90=53.8557 p99=70.913 p99.9=80.3693 max=92.7576 mean=48.7483 |
| T_fw_addon_json | n=4430 p50=9.6767 p90=12.7549 p99=15.6711 p99.9=19.6726 max=21.6505 mean=9.103 |
| client_ttft_sse | n=10325 p50=159.5375 p90=162.8274 p99=176.7647 p99.9=183.1876 max=203.1275 mean=159.2406 |
| provider_sched_err_last | n=14755 p50=0.0406 p90=0.0884 p99=0.116 p99.9=0.2176 max=0.3695 mean=0.0439 |
| provider_sched_err_max_per_stream | n=14755 p50=0.0975 p90=0.2007 p99=0.3255 p99.9=0.4482 max=0.6193 mean=0.1049 |
| provider_write_max | n=14755 p50=0.0182 p90=0.0263 p99=0.0365 p99.9=0.0461 max=0.0724 mean=0.0183 |

- release lag: 14755 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/ub-050/rv-pbu-lg-3/lg', 'samples': 300, 'busy_max': 15.200566098359591, 'busy_mean': 4.57, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/ub-050/rv-pbu-lg-4/lg', 'samples': 300, 'busy_max': 17.80005397267165, 'busy_mean': 4.53, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 19.402118068689557, 'busy_p95': 6.442996741504947}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/ub-050/rv-pbu-lg-3/lg', 'scheduled': 9375, 'recorded': 9375, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/ub-050/rv-pbu-lg-4/lg', 'scheduled': 9375, 'recorded': 9375, 'interrupted': False}]
- health olg rv-pbu-lg-3: gc_cycles=0 sched_latency_max_ms=0.057344 tcp={'TcpRetransSegs': 2, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 2, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 961324, 'TcpInSegs': 1445369}
- health olg rv-pbu-lg-4: gc_cycles=0 sched_latency_max_ms=0.16384 tcp={'TcpRetransSegs': 4, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 4, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 954335, 'TcpInSegs': 1444053}
- health synthprov rv-pbu-prov-2: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 3, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 3, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 2966259, 'TcpInSegs': 2235525}
