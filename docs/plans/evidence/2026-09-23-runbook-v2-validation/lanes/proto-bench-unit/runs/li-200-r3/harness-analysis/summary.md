# Harness analysis (sut, policy=none) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| error_rate_le_budget | FAIL |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 60000 over 300 s = 200.0 req/s (configured 200.0)
- qualified: 58930 (196.43 req/s); expected blocks: 0; errors: 1070 (rate 0.017833333333333333)
- error reasons: {'http_403': 1070, 'incomplete': 1070, 'unjoined': 1070, 'disposition_BLOCK': 1070, 'stage_dispatch_S': 1070, 'stage_out_S': 1070}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=60000 p50=0.0836 p90=0.0932 p99=0.1039 p99.9=0.1306 max=0.2698 mean=0.0838
- join: {'joined': 58930, 'by_nonce_fallback': 0, 'provider_records': 73704, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=58930 p50=10.8122 p90=14.2851 p99=18.069 p99.9=20.1729 max=35.4134 mean=10.6002 |
| T_addon_first | n=58930 p50=10.7717 p90=14.4373 p99=26.7705 p99.9=34.5992 max=54.3602 mean=10.7445 |
| T_release_lag_max | n=58930 p50=47.6588 p90=54.1146 p99=72.437 p99.9=78.1613 max=117.7137 mean=38.2681 |
| T_release_lag_max_arrival | n=58930 p50=28.6903 p90=34.1345 p99=38.5749 p99.9=42.5479 max=69.6222 mean=25.0047 |
| T_fw_addon | n=58930 p50=47.6588 p90=54.1146 p99=72.437 p99.9=78.1613 max=117.7137 mean=38.2681 |
| T_fw_addon_sse | n=41259 p50=50.4819 p90=55.5728 p99=73.1789 p99.9=85.5575 max=117.7137 mean=50.0777 |
| T_fw_addon_json | n=17671 p50=10.8794 p90=14.4585 p99=18.1672 p99.9=20.345 max=25.3199 mean=10.6943 |
| client_ttft_sse | n=41259 p50=160.7546 p90=164.4812 p99=178.9311 p99.9=185.5447 max=204.4331 mean=160.8066 |
| provider_sched_err_last | n=58930 p50=0.0343 p90=0.0862 p99=0.1159 p99.9=0.1398 max=0.199 mean=0.0405 |
| provider_sched_err_max_per_stream | n=58930 p50=0.1044 p90=0.1457 p99=0.1852 p99.9=0.2128 max=0.2945 mean=0.0952 |
| provider_write_max | n=58930 p50=0.0193 p90=0.0272 p99=0.0362 p99.9=0.0462 max=0.0917 mean=0.019 |

- release lag: 58930 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/li-200-r3/rv-pbu-lg-3/lg', 'samples': 300, 'busy_max': 11.280814027309027, 'busy_mean': 7.84, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/li-200-r3/rv-pbu-lg-4/lg', 'samples': 300, 'busy_max': 8.07245790689507, 'busy_mean': 7.58, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 23.50979211759251, 'busy_p95': 13.108131612865847}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/li-200-r3/rv-pbu-lg-3/lg', 'scheduled': 37500, 'recorded': 37500, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/li-200-r3/rv-pbu-lg-4/lg', 'scheduled': 37500, 'recorded': 37500, 'interrupted': False}]
- health olg rv-pbu-lg-3: gc_cycles=0 sched_latency_max_ms=0.16384 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 3457941, 'TcpInSegs': 5780488}
- health olg rv-pbu-lg-4: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 3466469, 'TcpInSegs': 5783292}
- health synthprov rv-pbu-prov-2: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 15, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 6, 'TcpExtTCPTimeouts': 8, 'TcpOutSegs': 11872098, 'TcpInSegs': 8061432}
