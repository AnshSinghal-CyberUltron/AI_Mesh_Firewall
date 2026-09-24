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
- qualified: 58936 (196.45 req/s); expected blocks: 0; errors: 1064 (rate 0.017733333333333334)
- error reasons: {'http_403': 1064, 'incomplete': 1064, 'unjoined': 1064, 'disposition_BLOCK': 1064, 'stage_dispatch_S': 1064, 'stage_out_S': 1064, 'stage_sem_U': 1}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=60000 p50=0.0839 p90=0.0932 p99=0.1038 p99.9=0.1227 max=0.3448 mean=0.084
- join: {'joined': 58936, 'by_nonce_fallback': 0, 'provider_records': 73710, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=58936 p50=10.8167 p90=14.2846 p99=17.837 p99.9=19.8651 max=56.8341 mean=10.5958 |
| T_addon_first | n=58936 p50=10.7722 p90=14.4629 p99=27.287 p99.9=35.1351 max=57.2224 mean=10.7519 |
| T_release_lag_max | n=58936 p50=47.6892 p90=54.1126 p99=72.5109 p99.9=78.5929 max=113.8458 mean=38.2562 |
| T_release_lag_max_arrival | n=58936 p50=28.7447 p90=34.172 p99=38.6826 p99.9=54.2503 max=79.3038 mean=25.0514 |
| T_fw_addon | n=58936 p50=47.6892 p90=54.1126 p99=72.5109 p99.9=78.5929 max=113.8458 mean=38.2562 |
| T_fw_addon_sse | n=41264 p50=50.4832 p90=55.5471 p99=73.2229 p99.9=87.3347 max=113.8458 mean=50.0662 |
| T_fw_addon_json | n=17672 p50=10.8941 p90=14.4219 p99=17.9626 p99.9=19.8294 max=49.172 mean=10.6799 |
| client_ttft_sse | n=41264 p50=160.7586 p90=164.5297 p99=179.9972 p99.9=185.9569 max=207.2281 mean=160.8236 |
| provider_sched_err_last | n=58936 p50=0.0345 p90=0.0858 p99=0.116 p99.9=0.1433 max=0.2052 mean=0.0406 |
| provider_sched_err_max_per_stream | n=58936 p50=0.1034 p90=0.1447 p99=0.184 p99.9=0.2171 max=0.5799 mean=0.0945 |
| provider_write_max | n=58936 p50=0.0189 p90=0.0256 p99=0.0354 p99.9=0.045 max=0.4254 mean=0.0185 |

- release lag: 58936 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/li-200-r2/rv-pbu-lg-3/lg', 'samples': 300, 'busy_max': 18.66497376557017, 'busy_mean': 7.86, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/li-200-r2/rv-pbu-lg-4/lg', 'samples': 300, 'busy_max': 20.421340875426107, 'busy_mean': 7.63, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 430, 'busy_max': 22.25205661195989, 'busy_p95': 12.885339059121549}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/li-200-r2/rv-pbu-lg-3/lg', 'scheduled': 37500, 'recorded': 37500, 'interrupted': False}, {'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs/li-200-r2/rv-pbu-lg-4/lg', 'scheduled': 37500, 'recorded': 37500, 'interrupted': False}]
- health olg rv-pbu-lg-3: gc_cycles=0 sched_latency_max_ms=0.458752 tcp={'TcpRetransSegs': 0, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 0, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 3467898, 'TcpInSegs': 5783107}
- health olg rv-pbu-lg-4: gc_cycles=0 sched_latency_max_ms=0.08192 tcp={'TcpRetransSegs': 2, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 1, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 3454161, 'TcpInSegs': 5781696}
- health synthprov rv-pbu-prov-2: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 39, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 32, 'TcpExtTCPTimeouts': 7, 'TcpOutSegs': 11873174, 'TcpInSegs': 8024697}
