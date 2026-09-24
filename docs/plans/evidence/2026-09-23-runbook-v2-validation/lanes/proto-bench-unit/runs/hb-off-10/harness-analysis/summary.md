# Harness analysis (sut, policy=none) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | PASS |
| error_rate_le_budget | FAIL |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 12000 over 300 s = 40.0 req/s (configured 40.0)
- qualified: 11817 (39.39 req/s); expected blocks: 0; errors: 183 (rate 0.01525)
- error reasons: {'http_403': 183, 'incomplete': 183, 'unjoined': 183, 'disposition_BLOCK': 183, 'stage_dispatch_S': 183}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=12000 p50=0.0853 p90=0.0955 p99=0.1066 p99.9=0.1257 max=0.1697 mean=0.0857
- join: {'joined': 11817, 'by_nonce_fallback': 0, 'provider_records': 14773, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=11817 p50=9.4168 p90=11.9626 p99=14.3821 p99.9=18.608 max=21.8382 mean=8.8807 |
| T_addon_first | n=11817 p50=9.3252 p90=11.8355 p99=14.219 p99.9=17.886 max=20.5295 mean=8.7847 |
| T_release_lag_max | n=11817 p50=12.5786 p90=16.684 p99=19.7263 p99.9=22.1015 max=24.9767 mean=12.8157 |
| T_release_lag_max_arrival | n=11817 p50=12.5786 p90=16.684 p99=19.7263 p99.9=22.1015 max=24.9767 mean=12.8157 |
| T_fw_addon | n=11817 p50=12.5786 p90=16.684 p99=19.7263 p99.9=22.1015 max=24.9767 mean=12.8157 |
| T_fw_addon_sse | n=11817 p50=12.5786 p90=16.684 p99=19.7263 p99.9=22.1015 max=24.9767 mean=12.8157 |
| T_fw_addon_json | n=0 |
| client_ttft_sse | n=11817 p50=159.3773 p90=161.8781 p99=164.2504 p99.9=167.8981 max=170.5508 mean=158.8324 |
| provider_sched_err_last | n=11817 p50=0.0467 p90=0.0874 p99=0.1095 p99.9=0.1291 max=0.1709 mean=0.0475 |
| provider_sched_err_max_per_stream | n=11817 p50=0.0949 p90=0.1265 p99=0.1535 p99.9=0.1933 max=0.9059 mean=0.0932 |
| provider_write_max | n=11817 p50=0.0182 p90=0.0261 p99=0.0334 p99.9=0.0465 max=0.1231 mean=0.0186 |

- release lag: 11817 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/hb-off-10/rv-pbu-lg-1/lg', 'samples': 300, 'busy_max': 8.00677584562316, 'busy_mean': 4.78, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/hb-off-10/rv-pbu-lg-2/lg', 'samples': 300, 'busy_max': 5.0586139295267785, 'busy_mean': 4.73, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 420, 'busy_max': 10.672049386934068, 'busy_p95': 7.106850750689375}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/hb-off-10/rv-pbu-lg-1/lg', 'scheduled': 7500, 'recorded': 7500, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/hb-off-10/rv-pbu-lg-2/lg', 'scheduled': 7500, 'recorded': 7500, 'interrupted': False}]
- health olg rv-pbu-lg-1: gc_cycles=0 sched_latency_max_ms=1.0485760000000002 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1109093, 'TcpInSegs': 1678096}
- health olg rv-pbu-lg-2: gc_cycles=0 sched_latency_max_ms=0.08192 tcp={'TcpRetransSegs': 1, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 1, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1109686, 'TcpInSegs': 1677500}
- health synthprov rv-pbu-prov-1: gc_cycles=0 sched_latency_max_ms=0.13107200000000002 tcp={'TcpRetransSegs': 1, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 1, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 3382493, 'TcpInSegs': 2791775}
