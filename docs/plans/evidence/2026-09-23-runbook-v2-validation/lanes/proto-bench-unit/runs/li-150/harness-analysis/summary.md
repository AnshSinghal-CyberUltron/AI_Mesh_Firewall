# Harness analysis (sut, policy=none) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| error_rate_le_budget | FAIL |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 45000 over 300 s = 150.0 req/s (configured 150.0)
- qualified: 44223 (147.41 req/s); expected blocks: 0; errors: 777 (rate 0.017266666666666666)
- error reasons: {'http_403': 777, 'incomplete': 777, 'unjoined': 777, 'disposition_BLOCK': 777, 'stage_dispatch_S': 777, 'stage_out_S': 777}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=45000 p50=0.0829 p90=0.0918 p99=0.1025 p99.9=0.1269 max=0.3051 mean=0.083
- join: {'joined': 44223, 'by_nonce_fallback': 0, 'provider_records': 55316, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=44223 p50=10.4442 p90=12.9791 p99=16.1855 p99.9=17.7979 max=31.0099 mean=9.8364 |
| T_addon_first | n=44223 p50=10.3993 p90=13.1438 p99=26.3619 p99.9=33.8463 max=52.7082 mean=9.9995 |
| T_release_lag_max | n=44223 p50=46.9241 p90=52.7317 p99=71.5558 p99.9=75.8964 max=108.6207 mean=37.436 |
| T_release_lag_max_arrival | n=44223 p50=27.7455 p90=32.8596 p99=36.3939 p99.9=42.6197 max=49.2776 mean=24.0997 |
| T_fw_addon | n=44223 p50=46.9241 p90=52.7317 p99=71.5558 p99.9=75.8964 max=108.6207 mean=37.436 |
| T_fw_addon_sse | n=30958 p50=50.1215 p90=54.3518 p99=72.0508 p99.9=85.1628 max=108.6207 mean=49.2267 |
| T_fw_addon_json | n=13265 p50=10.5094 p90=13.1236 p99=16.234 p99.9=17.8225 max=31.0099 mean=9.9187 |
| client_ttft_sse | n=30958 p50=160.3996 p90=163.1981 p99=178.1939 p99.9=184.6158 max=202.7581 mean=160.0764 |
| provider_sched_err_last | n=44223 p50=0.0381 p90=0.0866 p99=0.1144 p99.9=0.135 max=0.1769 mean=0.0425 |
| provider_sched_err_max_per_stream | n=44223 p50=0.0965 p90=0.136 p99=0.1711 p99.9=0.1985 max=0.2654 mean=0.0898 |
| provider_write_max | n=44223 p50=0.0182 p90=0.0265 p99=0.0353 p99.9=0.0457 max=0.0923 mean=0.0183 |

- release lag: 44223 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/li-150/rv-pbu-lg-3/lg', 'samples': 300, 'busy_max': 9.420322012179739, 'busy_mean': 6.25, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/li-150/rv-pbu-lg-4/lg', 'samples': 300, 'busy_max': 6.516710491145538, 'busy_mean': 6.05, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 23.996995021953673, 'busy_p95': 11.157603988650644}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/li-150/rv-pbu-lg-3/lg', 'scheduled': 28125, 'recorded': 28125, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/li-150/rv-pbu-lg-4/lg', 'scheduled': 28125, 'recorded': 28125, 'interrupted': False}]
- health olg rv-pbu-lg-3: gc_cycles=0 sched_latency_max_ms=0.13107200000000002 tcp={'TcpRetransSegs': 10, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 11, 'TcpOutSegs': 2606612, 'TcpInSegs': 4334345}
- health olg rv-pbu-lg-4: gc_cycles=0 sched_latency_max_ms=0.229376 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 2594019, 'TcpInSegs': 4330673}
- health synthprov rv-pbu-prov-2: gc_cycles=0 sched_latency_max_ms=0.13107200000000002 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 8897220, 'TcpInSegs': 6136223}
