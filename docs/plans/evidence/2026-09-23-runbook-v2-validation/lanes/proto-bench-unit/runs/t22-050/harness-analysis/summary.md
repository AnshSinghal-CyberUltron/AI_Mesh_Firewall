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
- qualified: 14750 (49.17 req/s); expected blocks: 0; errors: 250 (rate 0.016666666666666666)
- error reasons: {'incomplete': 250, 'unjoined': 250, 'http_403': 247, 'disposition_BLOCK': 247, 'stage_dispatch_S': 247, 'stage_out_S': 247, 'http_503': 3, 'disposition_missing': 3, 'stage_canon_missing': 3, 'stage_det_missing': 3, 'stage_sem_missing': 3, 'stage_resolve_missing': 3, 'stage_dispatch_missing': 3, 'stage_out_missing': 3, 'stage_audit_missing': 3, 'stage_sem_U': 1}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=15000 p50=0.0841 p90=0.0961 p99=0.1131 p99.9=0.2078 max=0.2682 mean=0.0854
- join: {'joined': 14750, 'by_nonce_fallback': 0, 'provider_records': 18452, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=14750 p50=13.3089 p90=16.7701 p99=19.3527 p99.9=23.8829 max=31.3865 mean=13.4249 |
| T_addon_first | n=14750 p50=13.2451 p90=17.0349 p99=31.0899 p99.9=37.1124 max=53.1637 mean=13.5776 |
| T_release_lag_max | n=1469 p50=51.7497 p90=57.1776 p99=74.6666 p99.9=78.3256 max=78.8447 mean=42.122 |
| T_release_lag_max_arrival | n=1469 p50=37.1411 p90=41.0688 p99=44.9067 p99.9=51.324 max=51.9356 mean=31.4481 |
| T_fw_addon | n=14750 p50=13.628 p90=18.9365 p99=57.1671 p99.9=74.6666 max=78.8447 mean=16.5833 |
| T_fw_addon_sse | n=10322 p50=13.767 p90=37.2459 p99=58.0677 p99.9=75.5784 max=78.8447 mean=17.9078 |
| T_fw_addon_json | n=4428 p50=13.4007 p90=16.8718 p99=19.3311 p99.9=23.6133 max=31.3865 mean=13.4959 |
| client_ttft_sse | n=10322 p50=163.2279 p90=167.1389 p99=182.792 p99.9=187.5772 max=203.2267 mean=163.6576 |
| provider_sched_err_last | n=14750 p50=0.043 p90=0.0888 p99=0.1128 p99.9=0.1366 max=0.2701 mean=0.0451 |
| provider_sched_err_max_per_stream | n=14750 p50=0.0919 p90=0.1361 p99=0.2428 p99.9=0.8236 max=1.3153 mean=0.0908 |
| provider_write_max | n=14750 p50=0.0176 p90=0.0241 p99=0.0349 p99.9=0.0467 max=0.0798 mean=0.0173 |

- release lag: 1469 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/t22-050/rv-pbu-lg-1/lg', 'samples': 300, 'busy_max': 17.85595304228368, 'busy_mean': 4.55, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/t22-050/rv-pbu-lg-2/lg', 'samples': 300, 'busy_max': 17.833963742139737, 'busy_mean': 4.36, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 18.550785189556397, 'busy_p95': 6.549572340287657}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/t22-050/rv-pbu-lg-1/lg', 'scheduled': 9375, 'recorded': 9375, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/t22-050/rv-pbu-lg-2/lg', 'scheduled': 9375, 'recorded': 9375, 'interrupted': False}]
- health olg rv-pbu-lg-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 3, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 3, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 960316, 'TcpInSegs': 1444403}
- health olg rv-pbu-lg-2: gc_cycles=0 sched_latency_max_ms=0.08192 tcp={'TcpRetransSegs': 1, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 1, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 965697, 'TcpInSegs': 1444697}
- health synthprov rv-pbu-prov-1: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 22, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 22, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 2965013, 'TcpInSegs': 2307331}
