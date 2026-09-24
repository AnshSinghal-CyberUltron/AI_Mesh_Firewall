# Harness analysis (sut, policy=none) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 7500 over 300 s = 25.0 req/s (configured 25.0)
- qualified: 7500 (25.0 req/s); expected blocks: 0; errors: 0 (rate 0.0)
- error reasons: {}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=7500 p50=0.0882 p90=0.0977 p99=0.1097 p99.9=0.1335 max=0.2559 mean=0.0886
- join: {'joined': 7500, 'by_nonce_fallback': 0, 'provider_records': 9375, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=7500 p50=403.7098 p90=3719.7949 p99=7216.9692 p99.9=8959.6196 max=10346.0577 mean=1261.7567 |
| T_addon_first | n=7500 p50=787.412 p90=1046.1925 p99=1295.0897 p99.9=1519.0313 max=1611.8872 mean=669.1735 |
| T_release_lag_max | n=725 p50=1186.81 p90=4141.6654 p99=7455.5913 p99.9=8895.4321 max=8895.4321 mean=1617.8371 |
| T_release_lag_max_arrival | n=725 p50=1186.81 p90=4141.6654 p99=7455.5913 p99.9=8895.4321 max=8895.4321 mean=1617.8371 |
| T_fw_addon | n=7500 p50=855.9936 p90=3724.2608 p99=7241.7508 p99.9=8959.6196 max=10346.0577 mean=1420.6552 |
| T_fw_addon_sse | n=5250 p50=1147.5857 p90=4415.414 p99=7533.7286 p99.9=9014.3362 max=10346.0577 mean=1937.1791 |
| T_fw_addon_json | n=2250 p50=206.7219 p90=303.1795 p99=493.7028 p99.9=581.1578 max=659.3318 mean=215.4328 |
| client_ttft_sse | n=5250 p50=1008.697 p90=1244.9162 p99=1484.828 p99.9=1693.6921 max=1761.9125 mean=1013.6856 |
| provider_sched_err_last | n=7500 p50=0.0526 p90=0.09 p99=0.1055 p99.9=0.1189 max=0.1244 mean=0.0515 |
| provider_sched_err_max_per_stream | n=7500 p50=0.0741 p90=0.1119 p99=0.1305 p99.9=0.1708 max=0.2155 mean=0.0724 |
| provider_write_max | n=7500 p50=0.0123 p90=0.0189 p99=0.0261 p99.9=0.0479 max=0.0751 mean=0.0134 |

- release lag: 725 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/v1-bench/runs/L1b-r25/lg-v1aug', 'samples': 300, 'busy_max': 16.133573444984663, 'busy_mean': 4.69, 'machine': 'c4-standard-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 412, 'busy_max': 15.052482640636256, 'busy_p95': 5.131989434638385}
- completeness: [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/v1-bench/runs/L1b-r25/lg-v1aug', 'scheduled': 9375, 'recorded': 9375, 'interrupted': False}]
- health olg L1b-r25: gc_cycles=0 sched_latency_max_ms=0.13107200000000002 tcp=None
- health synthprov rv-v1-prov-1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp={'TcpRetransSegs': 3931, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 3923, 'TcpExtTCPTimeouts': 9, 'TcpOutSegs': 1505551, 'TcpInSegs': 1493680}
