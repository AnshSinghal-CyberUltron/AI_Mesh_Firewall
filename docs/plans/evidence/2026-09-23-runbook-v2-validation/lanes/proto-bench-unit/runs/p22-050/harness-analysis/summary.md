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
- schedule drops (>5.0 ms): 0; lateness ms: n=15000 p50=0.0899 p90=0.1037 p99=0.1193 p99.9=0.1409 max=0.2282 mean=0.0904
- join: {'joined': 14755, 'by_nonce_fallback': 0, 'provider_records': 18458, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=14755 p50=9.5158 p90=12.3149 p99=15.1681 p99.9=18.5391 max=22.2373 mean=8.9522 |
| T_addon_first | n=14755 p50=9.4652 p90=12.6695 p99=25.7682 p99.9=32.7903 max=45.3365 mean=9.1047 |
| T_release_lag_max | n=1493 p50=46.1595 p90=53.1013 p99=70.8497 p99.9=75.9346 max=76.1724 mean=36.7264 |
| T_release_lag_max_arrival | n=1493 p50=31.5734 p90=36.5588 p99=39.9271 p99.9=41.9736 max=43.9053 mean=25.8481 |
| T_fw_addon | n=14755 p50=9.7924 p90=14.3356 p99=53.1113 p99.9=70.8497 max=76.1724 mean=12.0227 |
| T_fw_addon_sse | n=10327 p50=9.8687 p90=30.4743 p99=54.5525 p99.9=72.0856 max=76.1724 mean=13.3201 |
| T_fw_addon_json | n=4428 p50=9.585 p90=12.3262 p99=14.7438 p99.9=20.4079 max=22.2373 mean=8.9967 |
| client_ttft_sse | n=10327 p50=159.4592 p90=162.7557 p99=177.0975 p99.9=183.2838 max=195.3672 mean=159.1954 |
| provider_sched_err_last | n=14755 p50=0.0415 p90=0.088 p99=0.115 p99.9=0.1819 max=0.2614 mean=0.0444 |
| provider_sched_err_max_per_stream | n=14755 p50=0.0973 p90=0.1826 p99=0.306 p99.9=0.3898 max=0.6594 mean=0.1025 |
| provider_write_max | n=14755 p50=0.0182 p90=0.0258 p99=0.0367 p99.9=0.0485 max=0.104 mean=0.0182 |

- release lag: 1493 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22-050/rv-pbu-lg-1/lg', 'samples': 300, 'busy_max': 14.62284983436768, 'busy_mean': 4.61, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22-050/rv-pbu-lg-2/lg', 'samples': 300, 'busy_max': 17.725724295369016, 'busy_mean': 4.46, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 18.93568910152892, 'busy_p95': 6.532322501876752}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22-050/rv-pbu-lg-1/lg', 'scheduled': 9375, 'recorded': 9375, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22-050/rv-pbu-lg-2/lg', 'scheduled': 9375, 'recorded': 9375, 'interrupted': False}]
- health olg rv-pbu-lg-1: gc_cycles=0 sched_latency_max_ms=0.06553600000000001 tcp={'TcpRetransSegs': 8, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 2, 'TcpExtTCPTimeouts': 6, 'TcpOutSegs': 951994, 'TcpInSegs': 1445263}
- health olg rv-pbu-lg-2: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 951829, 'TcpInSegs': 1444798}
- health synthprov rv-pbu-prov-1: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 7, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 7, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 2966458, 'TcpInSegs': 2207937}
