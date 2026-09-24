# Harness analysis (sut, policy=none) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| error_rate_le_budget | FAIL |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 45542 over 300 s = 151.81 req/s (configured 150.0)
- qualified: 44661 (148.87 req/s); expected blocks: 0; errors: 881 (rate 0.019344780642044705)
- error reasons: {'incomplete': 881, 'unjoined': 881, 'http_403': 799, 'disposition_BLOCK': 799, 'stage_dispatch_S': 799, 'stage_out_S': 799, 'http_503': 82, 'disposition_missing': 82, 'stage_canon_missing': 82, 'stage_det_missing': 82, 'stage_sem_missing': 82, 'stage_resolve_missing': 82, 'stage_dispatch_missing': 82, 'stage_out_missing': 82, 'stage_audit_missing': 82}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=45542 p50=0.0832 p90=0.0935 p99=0.1055 p99.9=0.1254 max=0.269 mean=0.0826
- join: {'joined': 44661, 'by_nonce_fallback': 0, 'provider_records': 55752, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=44661 p50=10.7467 p90=15.302 p99=21.3516 p99.9=27.4598 max=37.8881 mean=10.7607 |
| T_addon_first | n=44661 p50=10.6963 p90=15.5128 p99=26.9796 p99.9=36.3885 max=65.7658 mean=10.9136 |
| T_release_lag_max | n=4580 p50=47.3882 p90=55.865 p99=72.8498 p99.9=79.4024 max=92.6822 mean=38.8271 |
| T_release_lag_max_arrival | n=4580 p50=34.8532 p90=42.0865 p99=47.952 p99.9=53.9894 max=59.6051 mean=29.5193 |
| T_fw_addon | n=44661 p50=11.1028 p90=19.9165 p99=56.0642 p99.9=72.9445 max=92.6822 mean=13.9441 |
| T_fw_addon_sse | n=31273 p50=11.2525 p90=35.443 p99=58.4065 p99.9=74.1378 max=92.6822 mean=15.2795 |
| T_fw_addon_json | n=13388 p50=10.8112 p90=15.3177 p99=21.1776 p99.9=27.4416 max=32.1146 mean=10.8247 |
| client_ttft_sse | n=31273 p50=160.6939 p90=165.6506 p99=179.7126 p99.9=187.3391 max=215.773 mean=160.9956 |
| provider_sched_err_last | n=44661 p50=0.0403 p90=0.0871 p99=0.1131 p99.9=0.135 max=0.2059 mean=0.0436 |
| provider_sched_err_max_per_stream | n=44661 p50=0.0967 p90=0.136 p99=0.1747 p99.9=0.2 max=0.2308 mean=0.09 |
| provider_write_max | n=44661 p50=0.0178 p90=0.024 p99=0.0342 p99.9=0.0459 max=0.1655 mean=0.0175 |

- release lag: 4580 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22rp-150/rv-pbu-lg-1/lg', 'samples': 300, 'busy_max': 10.149187960647554, 'busy_mean': 6.94, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22rp-150/rv-pbu-lg-2/lg', 'samples': 300, 'busy_max': 7.209269989086431, 'busy_mean': 6.64, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 12.047170620438019, 'busy_p95': 11.641056589727494}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22rp-150/rv-pbu-lg-1/lg', 'scheduled': 28409, 'recorded': 28409, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22rp-150/rv-pbu-lg-2/lg', 'scheduled': 28394, 'recorded': 28394, 'interrupted': False}]
- health olg rv-pbu-lg-1: gc_cycles=0 sched_latency_max_ms=0.08192 tcp={'TcpRetransSegs': 15, 'TcpExtTCPFastRetrans': 1, 'TcpExtTCPLossProbes': 14, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 2823511, 'TcpInSegs': 4368994}
- health olg rv-pbu-lg-2: gc_cycles=0 sched_latency_max_ms=0.08192 tcp={'TcpRetransSegs': 12, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 12, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 2832819, 'TcpInSegs': 4367202}
- health synthprov rv-pbu-prov-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 67, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 62, 'TcpExtTCPTimeouts': 6, 'TcpOutSegs': 8969262, 'TcpInSegs': 6552632}
