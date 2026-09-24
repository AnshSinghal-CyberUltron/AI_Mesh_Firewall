# Harness analysis (sut, policy=none) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| error_rate_le_budget | FAIL |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 60000 over 600 s = 100.0 req/s (configured 100.0)
- qualified: 59423 (99.04 req/s); expected blocks: 0; errors: 577 (rate 0.009616666666666667)
- error reasons: {'incomplete': 577, 'unjoined': 577, 'http_403': 551, 'disposition_BLOCK': 551, 'stage_dispatch_S': 551, 'stage_out_S': 551, 'http_503': 26, 'disposition_missing': 26, 'stage_canon_missing': 26, 'stage_det_missing': 26, 'stage_sem_missing': 26, 'stage_resolve_missing': 26, 'stage_dispatch_missing': 26, 'stage_out_missing': 26, 'stage_audit_missing': 26}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=60000 p50=0.0856 p90=0.095 p99=0.1063 p99.9=0.1222 max=0.3018 mean=0.0829
- join: {'joined': 59423, 'by_nonce_fallback': 0, 'provider_records': 66863, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=59423 p50=7.4377 p90=9.2765 p99=16.3239 p99.9=21.1683 max=29.5932 mean=7.5366 |
| T_addon_first | n=59423 p50=7.2876 p90=9.3982 p99=36.7171 p99.9=39.7469 max=69.3828 mean=7.896 |
| T_release_lag_max | n=5937 p50=67.9139 p90=94.8217 p99=99.3196 p99.9=127.6947 max=157.0822 mean=71.0149 |
| T_release_lag_max_arrival | n=5937 p50=46.4318 p90=49.0566 p99=54.6383 p99.9=60.2473 max=65.396 mean=46.5368 |
| T_fw_addon | n=59423 p50=7.881 p90=64.5617 p99=94.8207 p99.9=99.3196 max=157.0822 mean=14.4924 |
| T_fw_addon_sse | n=59423 p50=7.881 p90=64.5617 p99=94.8207 p99.9=99.3196 max=157.0822 mean=14.4924 |
| T_fw_addon_json | n=0 |
| client_ttft_sse | n=59423 p50=157.3227 p90=159.4391 p99=186.7505 p99.9=189.7863 max=219.4501 mean=157.9335 |
| provider_sched_err_last | n=59423 p50=0.0265 p90=0.0871 p99=0.1276 p99.9=0.1609 max=0.5264 mean=0.0373 |
| provider_sched_err_max_per_stream | n=59423 p50=0.1494 p90=0.1897 p99=0.2334 p99.9=0.6807 max=1.0795 mean=0.1507 |
| provider_write_max | n=59423 p50=0.0228 p90=0.0295 p99=0.0383 p99.9=0.0532 max=0.3177 mean=0.0235 |

- release lag: 5937 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/l22-100/rv-pbu-lg-1/lg', 'samples': 600, 'busy_max': 22.785877205226267, 'busy_mean': 9.69, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/l22-100/rv-pbu-lg-2/lg', 'samples': 600, 'busy_max': 22.457842318538603, 'busy_mean': 9.33, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 736, 'busy_max': 26.363450528817367, 'busy_p95': 12.75110640961784}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/l22-100/rv-pbu-lg-1/lg', 'scheduled': 33750, 'recorded': 33750, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/l22-100/rv-pbu-lg-2/lg', 'scheduled': 33750, 'recorded': 33750, 'interrupted': False}]
- health olg rv-pbu-lg-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 11449424, 'TcpInSegs': 13196446}
- health olg rv-pbu-lg-2: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 14, 'TcpExtTCPFastRetrans': 1, 'TcpExtTCPLossProbes': 8, 'TcpExtTCPTimeouts': 4, 'TcpOutSegs': 11437225, 'TcpInSegs': 13174127}
- health synthprov rv-pbu-prov-1: gc_cycles=0 sched_latency_max_ms=0.196608 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 26923869, 'TcpInSegs': 16127241}
