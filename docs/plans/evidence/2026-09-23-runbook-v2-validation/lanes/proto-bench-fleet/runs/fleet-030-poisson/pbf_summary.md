# fleet-030-poisson: strict FAIL | load-knee FAIL (sut, units=3, rate=30)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 8877 (29.59/s) qualified 8629 (28.76/s) FP-blocks 149 (0.01678) infra 99 (0.011152416356877323) drops 0 safety 0
infra reasons: {'http_503': 99, 'incomplete': 99, 'unjoined': 99, 'disposition_missing': 99, 'stage_canon_missing': 99, 'stage_det_missing': 99, 'stage_sem_missing': 99, 'stage_resolve_missing': 99, 'stage_dispatch_missing': 99, 'stage_out_missing': 99, 'stage_audit_missing': 99}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 99}

T_fw_addon: n=8629 p50=10.6044 p90=15.4541 p99=52.411 p99.9=71.3601 max=74.3216 mean=12.9023
T_fw_addon_nohold: n=8629 p50=10.4085 p90=13.5233 p99=16.3364 p99.9=22.1892 max=39.3484 mean=9.8807
T_fw_addon_sse: n=6037 p50=10.6714 p90=32.4238 p99=53.8291 p99.9=71.5131 max=74.3216 mean=14.1671
T_fw_addon_json: n=2592 p50=10.4708 p90=13.7003 p99=16.1375 p99.9=19.2858 max=35.9379 mean=9.9566
T_addon_first_sse: n=6037 p50=10.3072 p90=13.7708 p99=30.4023 p99.9=34.9521 max=49.7967 mean=10.1037
T_addon_total_sse: n=6037 p50=10.3775 p90=13.3858 p99=16.5568 p99.9=22.2507 max=39.3484 mean=9.8481
T_addon_total_json: n=2592 p50=10.4708 p90=13.7003 p99=16.1375 p99.9=19.2858 max=35.9379 mean=9.9566
T_release_lag_max: n=606 p50=50.1757 p90=53.8291 p99=71.5131 p99.9=74.3216 max=74.3216 mean=49.3745
lateness: n=8877 p50=0.0856 p90=0.0954 p99=0.1071 p99.9=0.1305 max=0.2506 mean=0.0855
loadgen: [{'vm': 'rv-pbf-lg-4', 'busy_max': 17.0, 'busy_mean': 4.4, 'late_max_us': 250, 'conn_opens': 178, 'max_inflight': 169}]  provider_cpu_busy_max: 20.82661866589386
gateway cores total 1.16 cpu-ms/req {'gateway': 39.287, 'workers': 34.959, 'owners': 4.298}
  rv-pbf-unit-6: cores {'launcher': 0.0, 'owner': 0.042, 'owner0': 0.042, 'redis': 0.001, 'worker': 0.346} worker util max 0.068 per-core max 0.064 mean 0.054 gpu {'0': {'n': 299, 'sm_mean': 2.6, 'sm_p95': 8.0, 'sm_max': 12.0}} t_input_p99 13.3038 per-worker admitted {'n': 6, 'min': 189, 'max': 638, 'mean': 492.8, 'max_over_mean': 1.295, 'sheds_per_worker': [0, 4, 4, 6, 8, 10]}
  rv-pbf-unit-8: cores {'launcher': 0.0, 'owner': 0.042, 'owner0': 0.042, 'redis': 0.001, 'worker': 0.34} worker util max 0.075 per-core max 0.062 mean 0.052 gpu {'0': {'n': 299, 'sm_mean': 3.1, 'sm_p95': 7.0, 'sm_max': 15.0}} t_input_p99 13.0417 per-worker admitted {'n': 6, 'min': 299, 'max': 698, 'mean': 492.8, 'max_over_mean': 1.416, 'sheds_per_worker': [0, 4, 5, 6, 8, 9]}
  rv-pbf-unit-9: cores {'launcher': 0.0, 'owner': 0.042, 'owner0': 0.042, 'redis': 0.001, 'worker': 0.344} worker util max 0.082 per-core max 0.058 mean 0.053 gpu {'0': {'n': 299, 'sm_mean': 3.0, 'sm_p95': 8.0, 'sm_max': 13.0}} t_input_p99 13.697 per-worker admitted {'n': 6, 'min': 243, 'max': 803, 'mean': 492.5, 'max_over_mean': 1.63, 'sheds_per_worker': [1, 5, 6, 6, 7, 10]}
W t_input_ns: {'n': 8770, 'mean_ms': 7.7368, 'p50_ms': 8.4541, 'p90_ms': 10.5513, 'p99_ms': 13.3038, 'p99.9_ms': 17.1704, 'max_cum_ms': 38.1453}
W t_tokenize_ns: {'n': 8869, 'mean_ms': 2.7189, 'p50_ms': 2.7361, 'p90_ms': 4.1452, 'p99_ms': 4.8169, 'p99.9_ms': 6.1276, 'max_cum_ms': 8.0436}
W t_guard_wait_ns: {'n': 8770, 'mean_ms': 4.4441, 'p50_ms': 4.948, 'p90_ms': 6.0621, 'p99_ms': 8.7163, 'p99.9_ms': 11.9931, 'max_cum_ms': 25.9861}
W guard_owner_rtt_ns: {'n': 8770, 'mean_ms': 4.6243, 'p50_ms': 5.1446, 'p90_ms': 6.1276, 'p99_ms': 8.5852, 'p99.9_ms': 11.862, 'max_cum_ms': 32.7484}
W guard_queue_ns: {'n': 8770, 'mean_ms': 0.1831, 'p50_ms': 0.1203, 'p90_ms': 0.1464, 'p99_ms': 2.8344, 'p99.9_ms': 5.3412, 'max_cum_ms': 11.7666}
W guard_exec_ns: {'n': 8770, 'mean_ms': 3.6948, 'p50_ms': 4.2926, 'p90_ms': 4.6203, 'p99_ms': 6.6519, 'p99.9_ms': 6.914, 'max_cum_ms': 9.3175}
W t_admit_ns: {'n': 8869, 'mean_ms': 0.1067, 'p50_ms': 0.0927, 'p90_ms': 0.1111, 'p99_ms': 0.8806, 'p99.9_ms': 1.0895, 'max_cum_ms': 7.8616}
W release_processing_ns: {'n': 1339967, 'mean_ms': 0.0664, 'p50_ms': 0.066, 'p90_ms': 0.0824, 'p99_ms': 0.106, 'p99.9_ms': 0.1423, 'max_cum_ms': 25.2296}
W loop_lag_ns: {'n': 53600, 'mean_ms': 0.7306, 'p50_ms': 0.0104, 'p90_ms': 1.4336, 'p99_ms': 7.1107, 'p99.9_ms': 9.6338, 'max_cum_ms': 28.3779}
W audit_batch_write_ns: {'n': 17417, 'mean_ms': 0.9707, 'p50_ms': 0.9134, 'p90_ms': 1.1878, 'p99_ms': 1.4664, 'p99.9_ms': 7.766, 'max_cum_ms': 28.6832}
W counts: {"admitted": 8869, "audit_enqueued": 17423, "audit_written": 17423, "background_round_trips": 10720, "disposition_ALLOW": 8621, "disposition_BLOCK": 149, "guard_windows": 14473, "lease_refills": 120, "provider_calls": 8621, "provider_connections_opened": 881, "requests_by_round_trips{n=\"0\"}": 8749, "requests_by_round_trips{n=\"1\"}": 120, "shared_state_round_trips": 120, "shed{reason=\"guard_queue\"}": 99}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 0.035}, "per_proc_util": {"nginx": [0.0, 0.003, 0.003, 0.004, 0.004, 0.005, 0.005, 0.005, 0.006]}, "per_core_util": {"max": 0.007, "mean": 0.005, "sum": 0.04, "n": 8}, "nic": null, "restarted": [], "loadavg_max": 0.14} nginx cpu-ms/req 1.174
redis: ops/s 279.2 ops/req 9.435 cpu cores 0.002 clients 92 mem 1237.9MB ping(us) {'n': 28484, 'p50_us': 433.3, 'p90_us': 463.8, 'p99_us': 561.5, 'p99.9_us': 993.3, 'max_us': 4272.7, 'mean_us': 440.9}
redis cmdstats: {'get': {'calls_per_s': 0.4, 'usec_per_call': 0.77}, 'info': {'calls_per_s': 0.0, 'usec_per_call': 130.0}, 'xadd': {'calls_per_s': 58.1, 'usec_per_call': 3.42}, 'hgetall': {'calls_per_s': 89.4, 'usec_per_call': 0.24}, 'ping': {'calls_per_s': 94.8, 'usec_per_call': 0.11}, 'decrby': {'calls_per_s': 0.4, 'usec_per_call': 0.43}, 'mget': {'calls_per_s': 35.8, 'usec_per_call': 0.42}, 'evalsha': {'calls_per_s': 0.4, 'usec_per_call': 15.04}}
wire: {'requests_in_window': 8877, 'unit_ip_bytes_per_req': {'unit_to_client_side': 55507.0, 'client_side_to_unit': 9792.3, 'unit_to_provider': 10349.0, 'provider_to_unit': 56180.9, 'unit_to_redis': 5919.0, 'redis_to_unit': 603.7}, 'edge_ip_bytes_per_req': {'edge_to_clients': 55631.0, 'clients_to_edge': 8697.4}, 'olg_resp_body_bytes_mean': {'sse': 67359.3, 'json': 1424.0}}
