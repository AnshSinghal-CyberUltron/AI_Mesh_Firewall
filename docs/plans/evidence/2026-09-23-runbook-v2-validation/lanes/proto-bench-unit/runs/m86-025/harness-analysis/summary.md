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
- qualified: 7423 (24.74 req/s); expected blocks: 0; errors: 77 (rate 0.010266666666666667)
- error reasons: {'http_403': 77, 'incomplete': 77, 'unjoined': 77, 'disposition_BLOCK': 77, 'stage_dispatch_S': 77, 'stage_out_S': 77}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=7500 p50=0.0874 p90=0.1004 p99=0.1196 p99.9=0.1551 max=0.2306 mean=0.0888
- join: {'joined': 7423, 'by_nonce_fallback': 0, 'provider_records': 9270, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=7423 p50=14.4259 p90=16.8767 p99=21.5567 p99.9=22.736 max=26.9034 mean=12.9624 |
| T_addon_first | n=7423 p50=14.3915 p90=17.4612 p99=28.4703 p99.9=40.7013 max=54.9631 mean=13.1396 |
| T_release_lag_max | n=723 p50=48.4888 p90=56.5837 p99=75.5779 p99.9=81.6638 max=81.6638 mean=40.4995 |
| T_release_lag_max_arrival | n=723 p50=33.3968 p90=40.8261 p99=46.2507 p99.9=50.6735 max=50.6735 mean=29.142 |
| T_fw_addon | n=7423 p50=14.692 p90=21.1839 p99=56.4524 p99.9=75.5779 max=81.6638 mean=15.9078 |
| T_fw_addon_sse | n=5187 p50=14.7737 p90=34.847 p99=60.885 p99.9=76.3128 max=81.6638 mean=17.1769 |
| T_fw_addon_json | n=2236 p50=14.5176 p90=16.8305 p99=21.5555 p99.9=23.0449 max=26.5744 mean=12.964 |
| client_ttft_sse | n=5187 p50=164.4107 p90=169.9134 p99=184.0398 p99.9=190.9725 max=205.0376 mean=163.2629 |
| provider_sched_err_last | n=7423 p50=0.0455 p90=0.0884 p99=0.1092 p99.9=0.1334 max=0.2831 mean=0.0473 |
| provider_sched_err_max_per_stream | n=7423 p50=0.0838 p90=0.13 p99=0.2553 p99.9=0.3293 max=0.3691 mean=0.0853 |
| provider_write_max | n=7423 p50=0.0165 p90=0.0241 p99=0.0351 p99.9=0.0476 max=0.2133 mean=0.0166 |

- release lag: 723 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/m86-025/rv-pbu-lg-1/lg', 'samples': 300, 'busy_max': 17.568425450446824, 'busy_mean': 4.23, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/m86-025/rv-pbu-lg-2/lg', 'samples': 300, 'busy_max': 16.564751630211184, 'busy_mean': 3.88, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 18.61406588680381, 'busy_p95': 5.182882420072598}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/m86-025/rv-pbu-lg-1/lg', 'scheduled': 4688, 'recorded': 4688, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/m86-025/rv-pbu-lg-2/lg', 'scheduled': 4687, 'recorded': 4687, 'interrupted': False}]
- health olg rv-pbu-lg-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 1, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 1, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 549365, 'TcpInSegs': 731635}
- health olg rv-pbu-lg-2: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 1, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 1, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 552111, 'TcpInSegs': 732209}
- health synthprov rv-pbu-prov-1: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 3, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 3, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1499797, 'TcpInSegs': 1238666}
