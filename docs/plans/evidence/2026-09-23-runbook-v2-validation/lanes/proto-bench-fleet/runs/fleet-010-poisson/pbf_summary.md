# fleet-010-poisson: strict FAIL | load-knee FAIL (sut, units=3, rate=10)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 6030 (10.05/s) qualified 5917 (9.86/s) FP-blocks 85 (0.0141) infra 28 (0.004643449419568822) drops 0 safety 0
infra reasons: {'http_503': 28, 'incomplete': 28, 'unjoined': 28, 'disposition_missing': 28, 'stage_canon_missing': 28, 'stage_det_missing': 28, 'stage_sem_missing': 28, 'stage_resolve_missing': 28, 'stage_dispatch_missing': 28, 'stage_out_missing': 28, 'stage_audit_missing': 28}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 28}

T_fw_addon: n=5917 p50=10.6711 p90=15.3258 p99=53.4808 p99.9=71.5873 max=79.751 mean=13.0009
T_fw_addon_nohold: n=5917 p50=10.4179 p90=13.1906 p99=15.8682 p99.9=20.9261 max=22.2202 mean=9.8871
T_fw_addon_sse: n=4141 p50=10.7708 p90=33.0183 p99=54.6282 p99.9=71.6438 max=79.751 mean=14.3123
T_fw_addon_json: n=1776 p50=10.4718 p90=13.3863 p99=16.2048 p99.9=20.8097 max=22.2202 mean=9.9434
T_addon_first_sse: n=4141 p50=10.3612 p90=13.7238 p99=30.2977 p99.9=33.9671 max=72.5936 mean=10.2068
T_addon_total_sse: n=4141 p50=10.4049 p90=13.1037 p99=15.8372 p99.9=20.9261 max=21.5101 mean=9.8629
T_addon_total_json: n=1776 p50=10.4718 p90=13.3863 p99=16.2048 p99.9=20.8097 max=22.2202 mean=9.9434
T_release_lag_max: n=417 p50=50.0189 p90=54.5617 p99=71.5873 p99.9=79.751 max=79.751 mean=49.8016
lateness: n=6030 p50=0.0856 p90=0.0963 p99=0.1097 p99.9=0.1377 max=0.252 mean=0.0861
loadgen: [{'vm': 'rv-pbf-lg-4', 'busy_max': 17.4, 'busy_mean': 3.7, 'late_max_us': 252, 'conn_opens': 82, 'max_inflight': 73}]  provider_cpu_busy_max: 14.761705989613949
gateway cores total 0.51 cpu-ms/req {'gateway': 51.257, 'workers': 46.52, 'owners': 4.647}
  rv-pbf-unit-6: cores {'launcher': 0.0, 'owner': 0.015, 'owner0': 0.015, 'redis': 0.001, 'worker': 0.156} worker util max 0.036 per-core max 0.028 mean 0.026 gpu {'0': {'n': 596, 'sm_mean': 1.0, 'sm_p95': 4.0, 'sm_max': 9.0}} t_input_p99 12.9106 per-worker admitted {'n': 6, 'min': 96, 'max': 555, 'mean': 335.0, 'max_over_mean': 1.657, 'sheds_per_worker': [0, 0, 1, 3, 3, 4]}
  rv-pbf-unit-8: cores {'launcher': 0.0, 'owner': 0.016, 'owner0': 0.016, 'redis': 0.001, 'worker': 0.156} worker util max 0.032 per-core max 0.027 mean 0.025 gpu {'0': {'n': 596, 'sm_mean': 1.3, 'sm_p95': 6.0, 'sm_max': 13.0}} t_input_p99 12.5174 per-worker admitted {'n': 6, 'min': 85, 'max': 470, 'mean': 335.2, 'max_over_mean': 1.402, 'sheds_per_worker': [0, 1, 1, 2, 3, 3]}
  rv-pbf-unit-9: cores {'launcher': 0.0, 'owner': 0.016, 'owner0': 0.016, 'redis': 0.001, 'worker': 0.155} worker util max 0.036 per-core max 0.026 mean 0.025 gpu {'0': {'n': 596, 'sm_mean': 1.1, 'sm_p95': 5.0, 'sm_max': 15.0}} t_input_p99 13.0417 per-worker admitted {'n': 6, 'min': 163, 'max': 567, 'mean': 334.8, 'max_over_mean': 1.693, 'sheds_per_worker': [0, 0, 1, 1, 2, 3]}
W t_input_ns: {'n': 6002, 'mean_ms': 7.612, 'p50_ms': 8.3558, 'p90_ms': 10.1581, 'p99_ms': 12.7795, 'p99.9_ms': 17.1704, 'max_cum_ms': 38.1453}
W t_tokenize_ns: {'n': 6030, 'mean_ms': 2.6247, 'p50_ms': 2.6378, 'p90_ms': 4.0141, 'p99_ms': 4.4892, 'p99.9_ms': 5.7999, 'max_cum_ms': 8.0436}
W t_guard_wait_ns: {'n': 6002, 'mean_ms': 4.4121, 'p50_ms': 5.0135, 'p90_ms': 5.4067, 'p99_ms': 7.8971, 'p99.9_ms': 11.862, 'max_cum_ms': 25.9861}
W guard_owner_rtt_ns: {'n': 6002, 'mean_ms': 4.5893, 'p50_ms': 5.2756, 'p90_ms': 5.6033, 'p99_ms': 7.766, 'p99.9_ms': 10.5513, 'max_cum_ms': 32.7484}
W guard_queue_ns: {'n': 6002, 'mean_ms': 0.1443, 'p50_ms': 0.1213, 'p90_ms': 0.1423, 'p99_ms': 0.3215, 'p99.9_ms': 4.4237, 'max_cum_ms': 11.7666}
W guard_exec_ns: {'n': 6002, 'mean_ms': 3.704, 'p50_ms': 4.4237, 'p90_ms': 4.6203, 'p99_ms': 6.6519, 'p99.9_ms': 6.8485, 'max_cum_ms': 9.3175}
W t_admit_ns: {'n': 6030, 'mean_ms': 0.1105, 'p50_ms': 0.0957, 'p90_ms': 0.1132, 'p99_ms': 0.9298, 'p99.9_ms': 1.1387, 'max_cum_ms': 7.8616}
W release_processing_ns: {'n': 920936, 'mean_ms': 0.0712, 'p50_ms': 0.0701, 'p90_ms': 0.0865, 'p99_ms': 0.1111, 'p99.9_ms': 0.1485, 'max_cum_ms': 25.2296}
W loop_lag_ns: {'n': 107280, 'mean_ms': 0.7056, 'p50_ms': 0.0566, 'p90_ms': 1.2534, 'p99_ms': 6.6519, 'p99.9_ms': 7.7005, 'max_cum_ms': 28.3779}
W audit_batch_write_ns: {'n': 11919, 'mean_ms': 1.0065, 'p50_ms': 0.9544, 'p90_ms': 1.2698, 'p99_ms': 1.4172, 'p99.9_ms': 5.7999, 'max_cum_ms': 28.6832}
W counts: {"admitted": 6030, "audit_enqueued": 11920, "audit_written": 11920, "background_round_trips": 21456, "disposition_ALLOW": 5917, "disposition_BLOCK": 85, "guard_windows": 9862, "lease_refills": 86, "provider_calls": 5917, "provider_connections_opened": 1053, "requests_by_round_trips{n=\"0\"}": 5944, "requests_by_round_trips{n=\"1\"}": 86, "shared_state_round_trips": 86, "shed{reason=\"guard_queue\"}": 28}
edge: {"window_s": 601.0, "cores_by_role": {"nginx": 0.012}, "per_proc_util": {"nginx": [0.0, 0.0, 0.001, 0.001, 0.001, 0.002, 0.002, 0.002, 0.002]}, "per_core_util": {"max": 0.003, "mean": 0.002, "sum": 0.02, "n": 8}, "nic": null, "restarted": [], "loadavg_max": 0.13} nginx cpu-ms/req 1.197
redis: ops/s 239.6 ops/req 23.838 cpu cores 0.002 clients 92 mem 1274.8MB ping(us) {'n': 56522, 'p50_us': 501.3, 'p90_us': 589.0, 'p99_us': 688.2, 'p99.9_us': 1367.8, 'max_us': 3838.3, 'mean_us': 524.3}
redis cmdstats: {'get': {'calls_per_s': 0.1, 'usec_per_call': 0.95}, 'info': {'calls_per_s': 0.0, 'usec_per_call': 183.0}, 'xadd': {'calls_per_s': 19.9, 'usec_per_call': 4.0}, 'hgetall': {'calls_per_s': 89.4, 'usec_per_call': 0.22}, 'ping': {'calls_per_s': 94.1, 'usec_per_call': 0.11}, 'decrby': {'calls_per_s': 0.1, 'usec_per_call': 0.44}, 'mget': {'calls_per_s': 35.8, 'usec_per_call': 0.41}, 'evalsha': {'calls_per_s': 0.1, 'usec_per_call': 19.08}}
wire: {'requests_in_window': 6030, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56158.1, 'client_side_to_unit': 10454.8, 'unit_to_provider': 10768.4, 'provider_to_unit': 56851.9, 'unit_to_redis': 7062.4, 'redis_to_unit': 1295.3}, 'edge_ip_bytes_per_req': {'edge_to_clients': 56277.5, 'clients_to_edge': 9697.9}, 'olg_resp_body_bytes_mean': {'sse': 67486.2, 'json': 1427.8}}
