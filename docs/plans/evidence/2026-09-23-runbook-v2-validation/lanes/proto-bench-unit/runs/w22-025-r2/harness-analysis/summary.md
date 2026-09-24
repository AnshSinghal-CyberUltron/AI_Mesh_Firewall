# Harness analysis (sut, policy=none) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| error_rate_le_budget | FAIL |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 7500 over 300 s = 25.0 req/s (configured 25.0)
- qualified: 7333 (24.44 req/s); expected blocks: 0; errors: 167 (rate 0.022266666666666667)
- error reasons: {'http_403': 167, 'incomplete': 167, 'unjoined': 167, 'disposition_BLOCK': 167, 'stage_dispatch_S': 167, 'stage_out_S': 167}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=7500 p50=0.0861 p90=0.1018 p99=0.1339 p99.9=0.2131 max=0.3724 mean=0.0851
- join: {'joined': 7333, 'by_nonce_fallback': 0, 'provider_records': 9161, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=7333 p50=14.3231 p90=14.9067 p99=19.2034 p99.9=22.0784 max=24.5168 mean=14.3749 |
| T_addon_first | n=7333 p50=14.2784 p90=15.0889 p99=33.0121 p99.9=34.526 max=53.8032 mean=14.6182 |
| T_release_lag_max | n=731 p50=54.0595 p90=57.6614 p99=74.5387 p99.9=94.6437 max=94.6437 mean=42.6923 |
| T_release_lag_max_arrival | n=731 p50=40.5773 p90=42.3967 p99=44.8934 p99.9=48.0135 max=48.0135 mean=32.283 |
| T_fw_addon | n=7333 p50=14.462 p90=17.0854 p99=57.6614 p99.9=74.5387 max=94.6437 mean=17.5552 |
| T_fw_addon_sse | n=5130 p50=14.4652 p90=34.0175 p99=73.0644 p99.9=74.6291 max=94.6437 mean=18.8653 |
| T_fw_addon_json | n=2203 p50=14.4559 p90=14.9952 p99=19.1932 p99.9=22.2868 max=24.5168 mean=14.5046 |
| client_ttft_sse | n=5130 p50=164.2456 p90=166.0236 p99=183.6878 p99.9=184.7793 max=203.8129 mean=164.7128 |
| provider_sched_err_last | n=7333 p50=0.0299 p90=0.0952 p99=0.2061 p99.9=0.3427 max=0.499 mean=0.0426 |
| provider_sched_err_max_per_stream | n=7333 p50=0.2636 p90=0.4123 p99=0.7006 p99.9=1.1085 max=1.2127 mean=0.234 |
| provider_write_max | n=7333 p50=0.0243 p90=0.0344 p99=0.0512 p99.9=0.1042 max=0.2386 mean=0.0258 |

- release lag: 731 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/w22-025-r2/rv-pbu-lg-1/lg', 'samples': 300, 'busy_max': 8.233940892401016, 'busy_mean': 4.81, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/w22-025-r2/rv-pbu-lg-2/lg', 'samples': 300, 'busy_max': 5.633441121687344, 'busy_mean': 4.67, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 17.751801780722165, 'busy_p95': 5.860100151862547}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/w22-025-r2/rv-pbu-lg-1/lg', 'scheduled': 4688, 'recorded': 4688, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/w22-025-r2/rv-pbu-lg-2/lg', 'scheduled': 4687, 'recorded': 4687, 'interrupted': False}]
- health olg rv-pbu-lg-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 6, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 1, 'TcpExtTCPTimeouts': 6, 'TcpOutSegs': 777998, 'TcpInSegs': 1268835}
- health olg rv-pbu-lg-2: gc_cycles=0 sched_latency_max_ms=0.13107200000000002 tcp={'TcpRetransSegs': 4, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 2, 'TcpExtTCPTimeouts': 1, 'TcpOutSegs': 768901, 'TcpInSegs': 1267643}
- health synthprov rv-pbu-prov-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 4, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 3, 'TcpExtTCPTimeouts': 1, 'TcpOutSegs': 2592898, 'TcpInSegs': 1486182}
