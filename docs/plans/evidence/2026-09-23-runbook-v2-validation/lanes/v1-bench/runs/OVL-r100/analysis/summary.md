# Harness analysis (sut, policy=none) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| error_rate_le_budget | FAIL |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 12000 over 120 s = 100.0 req/s (configured 100.0)
- qualified: 3446 (28.72 req/s); expected blocks: 0; errors: 8554 (rate 0.7128333333333333)
- error reasons: {'incomplete': 8553, 'disposition_missing': 7856, 'stage_auth_missing': 7856, 'stage_kill_switch_missing': 7856, 'stage_rate_limit_missing': 7856, 'stage_policy_missing': 7856, 'stage_input_scan_missing': 7856, 'stage_model_routing_missing': 7856, 'stage_model_input_missing': 7856, 'stage_model_output_missing': 7856, 'stage_output_guardrail_missing': 7856, 'timeout': 5202, 'content_mismatch': 5195, 'unjoined': 3351, 'http_422': 1845, 'http_503': 1506}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=12000 p50=0.0814 p90=0.0945 p99=0.1206 p99.9=0.1641 max=0.277 mean=0.0835
- join: {'joined': 8649, 'by_nonce_fallback': 0, 'provider_records': 9149, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=3446 p50=9736.9849 p90=103927.9308 p99=115505.3474 p99.9=118154.895 max=118341.024 mean=31002.9087 |
| T_addon_first | n=3446 p50=9537.3161 p90=51210.2109 p99=72927.0543 p99.9=83806.4549 max=92537.5802 mean=18011.5472 |
| T_release_lag_max | n=328 p50=9617.5442 p90=104515.0658 p99=114491.8496 p99.9=117248.616 max=117248.616 mean=29359.0074 |
| T_release_lag_max_arrival | n=328 p50=9617.5442 p90=104515.0658 p99=114491.8496 p99.9=117248.616 max=117248.616 mean=29359.0074 |
| T_fw_addon | n=3446 p50=9736.9849 p90=103927.9308 p99=115505.3474 p99.9=118154.895 max=118341.024 mean=31005.13 |
| T_fw_addon_sse | n=880 p50=100785.71 p90=113410.9322 p99=117709.8684 p99.9=118341.024 max=118341.024 mean=99500.8325 |
| T_fw_addon_json | n=2566 p50=7252.8131 p90=12875.3671 p99=15429.8396 p99.9=18279.2312 max=18734.3771 mean=7514.7877 |
| client_ttft_sse | n=880 p50=48468.191 p90=65707.4131 p99=77655.4149 p99.9=92687.6657 max=92687.6657 mean=48769.1956 |
| provider_sched_err_last | n=8649 p50=0.0504 p90=0.0869 p99=0.1043 p99.9=0.1143 max=0.144 mean=0.0492 |
| provider_sched_err_max_per_stream | n=8649 p50=0.0703 p90=0.1084 p99=0.144 p99.9=0.1758 max=0.1969 mean=0.0695 |
| provider_write_max | n=8649 p50=0.011 p90=0.0195 p99=0.0383 p99.9=0.0554 max=0.0803 mean=0.0128 |

- release lag: 330 sampled streams joined, 501 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/v1-bench/runs/OVL-r100/lg-v1aug', 'samples': 120, 'busy_max': 5.757260419305766, 'busy_mean': 2.22, 'machine': 'c4-standard-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 248, 'busy_max': 13.4965745083673, 'busy_p95': 7.057557373797774}
- completeness: [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/v1-bench/runs/OVL-r100/lg-v1aug', 'scheduled': 12500, 'recorded': 12500, 'interrupted': False}]
- health olg OVL-r100: gc_cycles=0 sched_latency_max_ms=0.196608 tcp=None
- health synthprov rv-v1-prov-2: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 61265, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 61265, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1482872, 'TcpInSegs': 1370672}
