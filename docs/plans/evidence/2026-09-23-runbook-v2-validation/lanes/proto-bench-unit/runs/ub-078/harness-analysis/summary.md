# Harness analysis (sut, policy=none) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| error_rate_le_budget | FAIL |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 23400 over 300 s = 78.0 req/s (configured 78.0)
- qualified: 22982 (76.61 req/s); expected blocks: 0; errors: 418 (rate 0.017863247863247864)
- error reasons: {'http_403': 418, 'incomplete': 418, 'unjoined': 418, 'disposition_BLOCK': 418, 'stage_dispatch_S': 418, 'stage_out_S': 418}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=23400 p50=0.0845 p90=0.0942 p99=0.1056 p99.9=0.1294 max=0.3391 mean=0.0844
- join: {'joined': 22982, 'by_nonce_fallback': 0, 'provider_records': 28758, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=22982 p50=9.5821 p90=12.3265 p99=15.3211 p99.9=19.8281 max=31.3745 mean=9.0271 |
| T_addon_first | n=22982 p50=9.5374 p90=12.6715 p99=25.7168 p99.9=32.8547 max=50.5043 mean=9.1972 |
| T_release_lag_max | n=22982 p50=46.208 p90=52.7358 p99=70.6024 p99.9=75.9252 max=106.7252 mean=36.8806 |
| T_release_lag_max_arrival | n=22982 p50=31.6782 p90=37.3574 p99=41.5053 p99.9=45.134 max=51.9819 mean=26.3872 |
| T_fw_addon | n=22982 p50=46.208 p90=52.7358 p99=70.6024 p99.9=75.9252 max=106.7252 mean=36.8806 |
| T_fw_addon_sse | n=16089 p50=49.314 p90=53.7153 p99=71.1566 p99.9=84.9767 max=106.7252 mean=48.7768 |
| T_fw_addon_json | n=6893 p50=9.6577 p90=12.5671 p99=15.5632 p99.9=20.7848 max=31.3745 mean=9.1136 |
| client_ttft_sse | n=16089 p50=159.541 p90=162.7631 p99=177.3205 p99.9=183.5133 max=200.536 mean=159.2808 |
| provider_sched_err_last | n=22982 p50=0.0462 p90=0.0877 p99=0.1087 p99.9=0.1251 max=0.1493 mean=0.0475 |
| provider_sched_err_max_per_stream | n=22982 p50=0.083 p90=0.1226 p99=0.1522 p99.9=0.1837 max=0.2135 mean=0.0808 |
| provider_write_max | n=22982 p50=0.0168 p90=0.0229 p99=0.0314 p99.9=0.0431 max=0.0678 mean=0.0163 |

- release lag: 22982 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/ub-078/rv-pbu-lg-3/lg', 'samples': 300, 'busy_max': 8.35679197181669, 'busy_mean': 4.97, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/ub-078/rv-pbu-lg-4/lg', 'samples': 300, 'busy_max': 5.113150134175792, 'busy_mean': 4.77, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 20.244410550214276, 'busy_p95': 7.691145886394157}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/ub-078/rv-pbu-lg-3/lg', 'scheduled': 14625, 'recorded': 14625, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/ub-078/rv-pbu-lg-4/lg', 'scheduled': 14625, 'recorded': 14625, 'interrupted': False}]
- health olg rv-pbu-lg-3: gc_cycles=0 sched_latency_max_ms=0.08192 tcp={'TcpRetransSegs': 2, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 2, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1437123, 'TcpInSegs': 2256587}
- health olg rv-pbu-lg-4: gc_cycles=0 sched_latency_max_ms=0.32768 tcp={'TcpRetransSegs': 1, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 1, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1434684, 'TcpInSegs': 2255315}
- health synthprov rv-pbu-prov-2: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 15, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 15, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 4630475, 'TcpInSegs': 3305486}
