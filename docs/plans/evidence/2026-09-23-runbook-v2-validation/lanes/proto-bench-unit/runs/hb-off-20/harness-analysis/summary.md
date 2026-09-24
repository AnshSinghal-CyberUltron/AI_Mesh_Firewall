# Harness analysis (sut, policy=none) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| error_rate_le_budget | FAIL |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 12000 over 300 s = 40.0 req/s (configured 40.0)
- qualified: 11826 (39.42 req/s); expected blocks: 0; errors: 174 (rate 0.0145)
- error reasons: {'http_403': 174, 'incomplete': 174, 'unjoined': 174, 'disposition_BLOCK': 174, 'stage_dispatch_S': 174}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=12000 p50=0.0859 p90=0.0973 p99=0.1098 p99.9=0.1364 max=0.237 mean=0.0862
- join: {'joined': 11826, 'by_nonce_fallback': 0, 'provider_records': 14783, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=11826 p50=9.4913 p90=12.1006 p99=14.8306 p99.9=19.2501 max=24.9672 mean=8.9519 |
| T_addon_first | n=11826 p50=9.3958 p90=11.9833 p99=14.5557 p99.9=18.5954 max=24.9828 mean=8.8516 |
| T_release_lag_max | n=11826 p50=12.8496 p90=17.0618 p99=20.3285 p99.9=23.4333 max=29.1674 mean=13.1215 |
| T_release_lag_max_arrival | n=11826 p50=12.8496 p90=17.0618 p99=20.3285 p99.9=23.4333 max=29.1674 mean=13.1215 |
| T_fw_addon | n=11826 p50=12.8496 p90=17.0618 p99=20.3285 p99.9=23.4333 max=29.1674 mean=13.1215 |
| T_fw_addon_sse | n=11826 p50=12.8496 p90=17.0618 p99=20.3285 p99.9=23.4333 max=29.1674 mean=13.1215 |
| T_fw_addon_json | n=0 |
| client_ttft_sse | n=11826 p50=159.4416 p90=162.0339 p99=164.6251 p99.9=168.653 max=174.9967 mean=158.8996 |
| provider_sched_err_last | n=11826 p50=0.0455 p90=0.0889 p99=0.1106 p99.9=0.125 max=0.17 mean=0.0476 |
| provider_sched_err_max_per_stream | n=11826 p50=0.0982 p90=0.1277 p99=0.1632 p99.9=3.5546 max=4.9036 mean=0.1016 |
| provider_write_max | n=11826 p50=0.0181 p90=0.0247 p99=0.0323 p99.9=0.0448 max=0.0936 mean=0.0182 |

- release lag: 11826 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/hb-off-20/rv-pbu-lg-1/lg', 'samples': 300, 'busy_max': 7.957895162698225, 'busy_mean': 4.89, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/hb-off-20/rv-pbu-lg-2/lg', 'samples': 300, 'busy_max': 4.998976708949964, 'busy_mean': 4.63, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 12.972529853863735, 'busy_p95': 6.875733554078078}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/hb-off-20/rv-pbu-lg-1/lg', 'scheduled': 7500, 'recorded': 7500, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/hb-off-20/rv-pbu-lg-2/lg', 'scheduled': 7500, 'recorded': 7500, 'interrupted': False}]
- health olg rv-pbu-lg-1: gc_cycles=0 sched_latency_max_ms=0.08192 tcp={'TcpRetransSegs': 21, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 3, 'TcpExtTCPTimeouts': 17, 'TcpOutSegs': 1107508, 'TcpInSegs': 1679654}
- health olg rv-pbu-lg-2: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1108233, 'TcpInSegs': 1677807}
- health synthprov rv-pbu-prov-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 3, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 3, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 3384214, 'TcpInSegs': 2815128}
