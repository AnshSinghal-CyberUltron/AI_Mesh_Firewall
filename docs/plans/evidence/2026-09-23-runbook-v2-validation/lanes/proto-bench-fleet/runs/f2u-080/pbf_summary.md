# f2u-080: strict FAIL | load-knee PASS (sut, units=2, rate=80)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 24000 (80.0/s) qualified 23616 (78.72/s) FP-blocks 383 (0.01596) infra 1 (4.1666666666666665e-05) drops 0 safety 0
infra reasons: {'http_503': 1, 'incomplete': 1, 'unjoined': 1, 'disposition_missing': 1, 'stage_canon_missing': 1, 'stage_det_missing': 1, 'stage_sem_missing': 1, 'stage_resolve_missing': 1, 'stage_dispatch_missing': 1, 'stage_out_missing': 1, 'stage_audit_missing': 1}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 1}

T_fw_addon: n=23616 p50=10.3946 p90=14.9791 p99=53.4892 p99.9=71.6661 max=91.0817 mean=12.6356
T_fw_addon_nohold: n=23616 p50=10.1622 p90=12.9906 p99=15.6611 p99.9=19.3861 max=34.7943 mean=9.6191
T_fw_addon_sse: n=16529 p50=10.455 p90=31.0386 p99=54.3372 p99.9=72.3803 max=91.0817 mean=13.8901
T_fw_addon_json: n=7087 p50=10.2488 p90=13.3066 p99=15.8377 p99.9=19.3861 max=21.2492 mean=9.71
T_addon_first_sse: n=16529 p50=10.0583 p90=13.3945 p99=27.3814 p99.9=33.5616 max=50.2644 mean=9.8043
T_addon_total_sse: n=16529 p50=10.125 p90=12.8613 p99=15.5541 p99.9=19.2769 max=34.7943 mean=9.5801
T_addon_total_json: n=7087 p50=10.2488 p90=13.3066 p99=15.8377 p99.9=19.3861 max=21.2492 mean=9.71
T_release_lag_max: n=1654 p50=49.8735 p90=54.3372 p99=72.3803 p99.9=75.9321 max=91.0817 mean=49.3386
lateness: n=24000 p50=0.0868 p90=0.097 p99=0.107 p99.9=0.1204 max=0.2577 mean=0.0869
loadgen: [{'vm': 'rv-pbf-lg-1', 'busy_max': 4.5, 'busy_mean': 4.2, 'late_max_us': 415, 'conn_opens': 151, 'max_inflight': 146}, {'vm': 'rv-pbf-lg-2', 'busy_max': 4.4, 'busy_mean': 4.1, 'late_max_us': 246, 'conn_opens': 151, 'max_inflight': 146}, {'vm': 'rv-pbf-lg-3', 'busy_max': 4.4, 'busy_mean': 4.1, 'late_max_us': 257, 'conn_opens': 151, 'max_inflight': 146}]  provider_cpu_busy_max: 16.929871191795552
gateway cores total 2.93 cpu-ms/req {'gateway': 36.71, 'workers': 32.585, 'owners': 4.114}
  rv-pbf-unit-2: cores {'launcher': 0.0, 'owner': 0.166, 'owner0': 0.07, 'owner1': 0.097, 'redis': 0.001, 'worker': 1.327} worker util max 0.107 per-core max 0.076 mean 0.065 gpu {'0': {'n': 298, 'sm_mean': 5.1, 'sm_p95': 12.0, 'sm_max': 17.0}, '1': {'n': 298, 'sm_mean': 7.4, 'sm_p95': 15.0, 'sm_max': 20.0}} t_input_p99 12.7795 per-worker admitted {'n': 18, 'min': 225, 'max': 1031, 'mean': 665.4, 'max_over_mean': 1.549, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
  rv-pbf-unit-3: cores {'launcher': 0.0, 'owner': 0.162, 'owner0': 0.086, 'owner1': 0.076, 'redis': 0.001, 'worker': 1.272} worker util max 0.098 per-core max 0.071 mean 0.062 gpu {'0': {'n': 298, 'sm_mean': 6.6, 'sm_p95': 13.0, 'sm_max': 19.0}, '1': {'n': 298, 'sm_mean': 5.9, 'sm_p95': 13.0, 'sm_max': 18.0}} t_input_p99 12.3863 per-worker admitted {'n': 18, 'min': 342, 'max': 1026, 'mean': 666.2, 'max_over_mean': 1.54, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1]}
W t_input_ns: {'n': 23968, 'mean_ms': 7.4934, 'p50_ms': 8.2248, 'p90_ms': 10.1581, 'p99_ms': 12.6484, 'p99.9_ms': 15.7942, 'max_cum_ms': 36.429}
W t_tokenize_ns: {'n': 23969, 'mean_ms': 2.7322, 'p50_ms': 2.7361, 'p90_ms': 4.1779, 'p99_ms': 4.7514, 'p99.9_ms': 6.1276, 'max_cum_ms': 8.9355}
W t_guard_wait_ns: {'n': 23968, 'mean_ms': 4.2225, 'p50_ms': 4.7514, 'p90_ms': 5.2101, 'p99_ms': 7.3728, 'p99.9_ms': 11.4688, 'max_cum_ms': 29.7032}
W guard_owner_rtt_ns: {'n': 23968, 'mean_ms': 4.3924, 'p50_ms': 4.948, 'p90_ms': 5.4067, 'p99_ms': 7.5694, 'p99.9_ms': 10.5513, 'max_cum_ms': 28.7454}
W guard_queue_ns: {'n': 23968, 'mean_ms': 0.1088, 'p50_ms': 0.107, 'p90_ms': 0.1285, 'p99_ms': 0.1546, 'p99.9_ms': 0.8479, 'max_cum_ms': 16.1001}
W guard_exec_ns: {'n': 23968, 'mean_ms': 3.6025, 'p50_ms': 4.2271, 'p90_ms': 4.4892, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 8.1548}
W t_admit_ns: {'n': 23969, 'mean_ms': 0.0934, 'p50_ms': 0.0886, 'p90_ms': 0.1029, 'p99_ms': 0.1295, 'p99.9_ms': 1.0117, 'max_cum_ms': 21.7487}
W release_processing_ns: {'n': 3663696, 'mean_ms': 0.0634, 'p50_ms': 0.0632, 'p90_ms': 0.0794, 'p99_ms': 0.1009, 'p99.9_ms': 0.1306, 'max_cum_ms': 26.8838}
W loop_lag_ns: {'n': 107230, 'mean_ms': 0.7289, 'p50_ms': 0.0068, 'p90_ms': 1.5647, 'p99_ms': 7.3728, 'p99.9_ms': 9.2406, 'max_cum_ms': 27.5291}
W audit_batch_write_ns: {'n': 47542, 'mean_ms': 0.9875, 'p50_ms': 0.938, 'p90_ms': 1.1551, 'p99_ms': 1.4664, 'p99.9_ms': 7.3073, 'max_cum_ms': 40.9418}
W counts: {"admitted": 23969, "audit_enqueued": 47555, "audit_written": 47556, "background_round_trips": 21446, "disposition_ALLOW": 23585, "disposition_BLOCK": 383, "guard_windows": 39570, "lease_refills": 110, "provider_calls": 23585, "provider_connections_opened": 2719, "requests_by_round_trips{n=\"0\"}": 23859, "requests_by_round_trips{n=\"1\"}": 110, "shared_state_round_trips": 110, "shed{reason=\"guard_queue\"}": 1}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 0.09}, "per_proc_util": {"nginx": [0.0, 0.004, 0.004, 0.004, 0.004, 0.004, 0.005, 0.006, 0.006, 0.006, 0.006, 0.006, 0.006, 0.006, 0.007, 0.007, 0.008]}, "per_core_util": {"max": 0.006, "mean": 0.006, "sum": 0.1, "n": 16}, "nic": null, "restarted": [], "loadavg_max": 0.22} nginx cpu-ms/req 1.132
redis: ops/s 755.0 ops/req 9.437 cpu cores 0.006 clients 291 mem 655.3MB ping(us) {'n': 28125, 'p50_us': 555.1, 'p90_us': 633.5, 'p99_us': 710.7, 'p99.9_us': 1817.1, 'max_us': 4677.8, 'mean_us': 572.4}
redis cmdstats: {'info': {'calls_per_s': 0.0, 'usec_per_call': 131.0}, 'ping': {'calls_per_s': 93.6, 'usec_per_call': 0.1}, 'evalsha': {'calls_per_s': 0.4, 'usec_per_call': 11.28}, 'xadd': {'calls_per_s': 158.5, 'usec_per_call': 3.59}, 'mget': {'calls_per_s': 143.3, 'usec_per_call': 0.37}, 'hgetall': {'calls_per_s': 358.4, 'usec_per_call': 0.2}, 'get': {'calls_per_s': 0.4, 'usec_per_call': 0.62}, 'decrby': {'calls_per_s': 0.4, 'usec_per_call': 0.37}}
wire: {'requests_in_window': 24000, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56147.2, 'client_side_to_unit': 9049.0, 'unit_to_provider': 9761.3, 'provider_to_unit': 56833.1, 'unit_to_redis': 5745.6, 'redis_to_unit': 442.3}, 'edge_ip_bytes_per_req': {'edge_to_clients': 56265.6, 'clients_to_edge': 8364.4}, 'olg_resp_body_bytes_mean': {'sse': 67303.1, 'json': 1433.2}}
