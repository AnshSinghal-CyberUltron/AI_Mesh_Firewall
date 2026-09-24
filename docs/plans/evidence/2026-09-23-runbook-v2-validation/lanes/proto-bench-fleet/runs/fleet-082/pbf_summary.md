# fleet-082: strict FAIL | load-knee FAIL (sut, units=3, rate=82)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 24600 (82.0/s) qualified 24094 (80.31/s) FP-blocks 444 (0.01805) infra 62 (0.0025203252032520327) drops 0 safety 0
infra reasons: {'http_503': 62, 'incomplete': 62, 'unjoined': 62, 'disposition_missing': 62, 'stage_canon_missing': 62, 'stage_det_missing': 62, 'stage_sem_missing': 62, 'stage_resolve_missing': 62, 'stage_dispatch_missing': 62, 'stage_out_missing': 62, 'stage_audit_missing': 62}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 62}

T_fw_addon: n=24094 p50=10.8625 p90=16.3009 p99=54.2225 p99.9=71.7477 max=94.204 mean=13.2581
T_fw_addon_nohold: n=24094 p50=10.5683 p90=13.6035 p99=17.055 p99.9=21.3137 max=40.9799 mean=10.0829
T_fw_addon_sse: n=16868 p50=10.9822 p90=34.6122 p99=55.5877 p99.9=72.2465 max=94.204 mean=14.5896
T_fw_addon_json: n=7226 p50=10.6106 p90=13.8148 p99=16.9839 p99.9=20.3436 max=23.5852 mean=10.1499
T_addon_first_sse: n=16868 p50=10.4963 p90=13.9509 p99=27.6982 p99.9=35.4936 max=54.1771 mean=10.2941
T_addon_total_sse: n=16868 p50=10.5493 p90=13.5024 p99=17.0714 p99.9=21.9871 max=40.9799 mean=10.0542
T_addon_total_json: n=7226 p50=10.6106 p90=13.8148 p99=16.9839 p99.9=20.3436 max=23.5852 mean=10.1499
T_release_lag_max: n=1759 p50=50.3345 p90=55.3659 p99=72.2308 p99.9=79.2588 max=94.204 mean=49.9142
lateness: n=24600 p50=0.0869 p90=0.0963 p99=0.107 p99.9=0.1333 max=0.22 mean=0.0866
loadgen: [{'vm': 'rv-pbf-lg-4', 'busy_max': 6.8, 'busy_mean': 6.4, 'late_max_us': 696, 'conn_opens': 425, 'max_inflight': 411}]  provider_cpu_busy_max: 32.378732154009725
gateway cores total 2.91 cpu-ms/req {'gateway': 35.661, 'workers': 31.476, 'owners': 4.174}
  rv-pbf-unit-6: cores {'launcher': 0.0, 'owner': 0.115, 'owner0': 0.115, 'redis': 0.001, 'worker': 0.867} worker util max 0.165 per-core max 0.135 mean 0.127 gpu {'0': {'n': 298, 'sm_mean': 8.3, 'sm_p95': 15.0, 'sm_max': 18.0}} t_input_p99 13.566 per-worker admitted {'n': 6, 'min': 1239, 'max': 1551, 'mean': 1365.8, 'max_over_mean': 1.136, 'sheds_per_worker': [1, 3, 3, 3, 4, 4]}
  rv-pbf-unit-8: cores {'launcher': 0.0, 'owner': 0.113, 'owner0': 0.113, 'redis': 0.001, 'worker': 0.854} worker util max 0.156 per-core max 0.134 mean 0.124 gpu {'0': {'n': 298, 'sm_mean': 8.2, 'sm_p95': 15.0, 'sm_max': 17.0}} t_input_p99 13.566 per-worker admitted {'n': 6, 'min': 1165, 'max': 1512, 'mean': 1365.0, 'max_over_mean': 1.108, 'sheds_per_worker': [2, 2, 3, 4, 4, 8]}
  rv-pbf-unit-9: cores {'launcher': 0.0, 'owner': 0.113, 'owner0': 0.113, 'redis': 0.001, 'worker': 0.852} worker util max 0.17 per-core max 0.134 mean 0.123 gpu {'0': {'n': 298, 'sm_mean': 8.4, 'sm_p95': 15.0, 'sm_max': 24.0}} t_input_p99 13.566 per-worker admitted {'n': 6, 'min': 1000, 'max': 1672, 'mean': 1365.7, 'max_over_mean': 1.224, 'sheds_per_worker': [0, 1, 2, 4, 6, 8]}
W t_input_ns: {'n': 24518, 'mean_ms': 7.815, 'p50_ms': 8.4541, 'p90_ms': 10.6824, 'p99_ms': 13.566, 'p99.9_ms': 17.1704, 'max_cum_ms': 38.1453}
W t_tokenize_ns: {'n': 24580, 'mean_ms': 2.8912, 'p50_ms': 2.9, 'p90_ms': 4.4892, 'p99_ms': 5.2101, 'p99.9_ms': 5.9965, 'max_cum_ms': 7.4456}
W t_guard_wait_ns: {'n': 24518, 'mean_ms': 4.3401, 'p50_ms': 4.8824, 'p90_ms': 5.4067, 'p99_ms': 8.0282, 'p99.9_ms': 12.1242, 'max_cum_ms': 16.0843}
W guard_owner_rtt_ns: {'n': 24518, 'mean_ms': 4.5238, 'p50_ms': 5.079, 'p90_ms': 5.6689, 'p99_ms': 7.9626, 'p99.9_ms': 11.7309, 'max_cum_ms': 32.7484}
W guard_queue_ns: {'n': 24518, 'mean_ms': 0.1213, 'p50_ms': 0.1172, 'p90_ms': 0.1464, 'p99_ms': 0.1833, 'p99.9_ms': 0.8561, 'max_cum_ms': 4.6197}
W guard_exec_ns: {'n': 24518, 'mean_ms': 3.6496, 'p50_ms': 4.2926, 'p90_ms': 4.6203, 'p99_ms': 6.7174, 'p99.9_ms': 6.8485, 'max_cum_ms': 9.3175}
W t_admit_ns: {'n': 24579, 'mean_ms': 0.1038, 'p50_ms': 0.0906, 'p90_ms': 0.1121, 'p99_ms': 0.8643, 'p99.9_ms': 1.0281, 'max_cum_ms': 6.6699}
W release_processing_ns: {'n': 3755606, 'mean_ms': 0.0646, 'p50_ms': 0.0637, 'p90_ms': 0.0835, 'p99_ms': 0.108, 'p99.9_ms': 0.1382, 'max_cum_ms': 1.5571}
W loop_lag_ns: {'n': 53610, 'mean_ms': 0.773, 'p50_ms': 0.0084, 'p90_ms': 2.9327, 'p99_ms': 7.8316, 'p99.9_ms': 10.2892, 'max_cum_ms': 26.0693}
W audit_batch_write_ns: {'n': 48579, 'mean_ms': 0.9576, 'p50_ms': 0.8888, 'p90_ms': 1.1059, 'p99_ms': 2.007, 'p99.9_ms': 8.2903, 'max_cum_ms': 28.6832}
W counts: {"admitted": 24579, "audit_enqueued": 48608, "audit_written": 48609, "background_round_trips": 10722, "disposition_ALLOW": 24074, "disposition_BLOCK": 444, "guard_windows": 40253, "lease_refills": 340, "provider_calls": 24074, "provider_connections_opened": 3582, "requests_by_round_trips{n=\"0\"}": 24239, "requests_by_round_trips{n=\"1\"}": 340, "shared_state_round_trips": 340, "shed{reason=\"guard_queue\"}": 62}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 0.107}, "per_proc_util": {"nginx": [0.0, 0.011, 0.011, 0.012, 0.013, 0.014, 0.014, 0.015, 0.016]}, "per_core_util": {"max": 0.016, "mean": 0.014, "sum": 0.11, "n": 8}, "nic": null, "restarted": [], "loadavg_max": 0.2} nginx cpu-ms/req 1.306
redis: ops/s 384.0 ops/req 4.683 cpu cores 0.005 clients 91 mem 721.0MB ping(us) {'n': 28114, 'p50_us': 571.7, 'p90_us': 596.0, 'p99_us': 632.1, 'p99.9_us': 1469.3, 'max_us': 5209.2, 'mean_us': 575.9}
redis cmdstats: {'get': {'calls_per_s': 1.1, 'usec_per_call': 0.58}, 'info': {'calls_per_s': 0.0, 'usec_per_call': 145.0}, 'xadd': {'calls_per_s': 161.9, 'usec_per_call': 2.68}, 'hgetall': {'calls_per_s': 89.3, 'usec_per_call': 0.23}, 'ping': {'calls_per_s': 93.6, 'usec_per_call': 0.11}, 'decrby': {'calls_per_s': 1.1, 'usec_per_call': 0.39}, 'mget': {'calls_per_s': 35.7, 'usec_per_call': 0.39}, 'evalsha': {'calls_per_s': 1.1, 'usec_per_call': 10.79}}
wire: {'requests_in_window': 24600, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56120.4, 'client_side_to_unit': 9272.6, 'unit_to_provider': 9995.5, 'provider_to_unit': 56803.6, 'unit_to_redis': 5576.0, 'redis_to_unit': 376.6}, 'edge_ip_bytes_per_req': {'edge_to_clients': 56235.3, 'clients_to_edge': 7967.4}, 'olg_resp_body_bytes_mean': {'sse': 67594.4, 'json': 1420.4}}
