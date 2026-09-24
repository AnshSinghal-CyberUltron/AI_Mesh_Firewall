# Harness analysis (sut, policy=none) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| error_rate_le_budget | FAIL |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 23523 over 300 s = 78.41 req/s (configured 78.0)
- qualified: 22287 (74.29 req/s); expected blocks: 0; errors: 1236 (rate 0.05254431832674404)
- error reasons: {'incomplete': 1236, 'unjoined': 1236, 'http_503': 821, 'disposition_missing': 821, 'stage_canon_missing': 821, 'stage_det_missing': 821, 'stage_sem_missing': 821, 'stage_resolve_missing': 821, 'stage_dispatch_missing': 821, 'stage_out_missing': 821, 'stage_audit_missing': 821, 'http_403': 415, 'disposition_BLOCK': 415, 'stage_dispatch_S': 415, 'stage_out_S': 415}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=23523 p50=0.0827 p90=0.0928 p99=0.1038 p99.9=0.1251 max=0.2251 mean=0.0823
- join: {'joined': 22287, 'by_nonce_fallback': 0, 'provider_records': 27941, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=22287 p50=9.8368 p90=13.4176 p99=17.5444 p99.9=21.868 max=26.0148 mean=9.4761 |
| T_addon_first | n=22287 p50=9.7911 p90=13.4972 p99=25.5071 p99.9=33.7381 max=53.5936 mean=9.6176 |
| T_release_lag_max | n=2234 p50=46.4519 p90=53.5891 p99=70.5239 p99.9=75.3046 max=89.7511 mean=37.6314 |
| T_release_lag_max_arrival | n=2234 p50=33.0696 p90=39.2188 p99=43.4835 p99.9=47.0492 max=49.6274 mean=27.6773 |
| T_fw_addon | n=22287 p50=10.101 p90=16.0995 p99=53.5936 p99.9=70.5239 max=89.7511 mean=12.544 |
| T_fw_addon_sse | n=15587 p50=10.187 p90=31.8543 p99=55.2564 p99.9=70.765 max=89.7511 mean=13.8276 |
| T_fw_addon_json | n=6700 p50=9.9251 p90=13.5087 p99=17.6344 p99.9=21.067 max=26.0148 mean=9.5579 |
| client_ttft_sse | n=15587 p50=159.7842 p90=163.5435 p99=178.5928 p99.9=184.1105 max=203.6702 mean=159.6902 |
| provider_sched_err_last | n=22287 p50=0.0449 p90=0.0881 p99=0.1103 p99.9=0.1267 max=0.1763 mean=0.047 |
| provider_sched_err_max_per_stream | n=22287 p50=0.084 p90=0.1245 p99=0.1589 p99.9=0.1925 max=0.2393 mean=0.0814 |
| provider_write_max | n=22287 p50=0.0164 p90=0.0229 p99=0.0332 p99.9=0.0469 max=0.0886 mean=0.0161 |

- release lag: 2234 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22p-078/rv-pbu-lg-1/lg', 'samples': 300, 'busy_max': 8.63497589533152, 'busy_mean': 5.2, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22p-078/rv-pbu-lg-2/lg', 'samples': 300, 'busy_max': 5.532005086986002, 'busy_mean': 4.93, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 17.50598245343271, 'busy_p95': 8.067666124265315}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22p-078/rv-pbu-lg-1/lg', 'scheduled': 14604, 'recorded': 14604, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/p22p-078/rv-pbu-lg-2/lg', 'scheduled': 14828, 'recorded': 14828, 'interrupted': False}]
- health olg rv-pbu-lg-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 19, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 14, 'TcpExtTCPTimeouts': 6, 'TcpOutSegs': 1465326, 'TcpInSegs': 2175586}
- health olg rv-pbu-lg-2: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 5, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 5, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1498474, 'TcpInSegs': 2204782}
- health synthprov rv-pbu-prov-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 26, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 21, 'TcpExtTCPTimeouts': 6, 'TcpOutSegs': 4494419, 'TcpInSegs': 3427036}
