# uz4-700: strict FAIL | load-knee FAIL (sut, units=4, rate=700)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 210000 (700.0/s) qualified 206405 (688.02/s) FP-blocks 3589 (0.01709) infra 6 (2.857142857142857e-05) drops 0 safety 0
infra reasons: {'block_on_unavailable_sem': 6}
infra detail: {'403 http_403 type=policy_violation code=blocked_by_policy': 6}

T_fw_addon: n=206405 p50=48.2641 p90=55.7897 p99=72.921 p99.9=83.1772 max=122.4162 mean=39.3711
T_fw_addon_nohold: n=206405 p50=11.3059 p90=15.0571 p99=20.4946 p99.9=26.8826 max=56.8192 mean=11.1255
T_fw_addon_sse: n=144530 p50=51.2586 p90=57.6487 p99=73.8367 p99.9=87.0671 max=122.4162 mean=51.434
T_fw_addon_json: n=61875 p50=11.3729 p90=15.1252 p99=20.5223 p99.9=27.0706 max=53.6525 mean=11.1941
T_addon_first_sse: n=144530 p50=11.223 p90=15.3313 p99=30.3697 p99.9=37.4006 max=59.8636 mean=11.3378
T_addon_total_sse: n=144530 p50=11.2795 p90=15.0247 p99=20.4822 p99.9=26.8417 max=56.8192 mean=11.0961
T_addon_total_json: n=61875 p50=11.3729 p90=15.1252 p99=20.5223 p99.9=27.0706 max=53.6525 mean=11.1941
T_release_lag_max: n=144530 p50=51.2586 p90=57.6487 p99=73.8367 p99.9=87.0671 max=122.4162 mean=51.434
lateness: n=210000 p50=0.0877 p90=0.0994 p99=0.1143 p99.9=0.1369 max=0.3305 mean=0.0872
loadgen: [{'vm': 'rv-pbf-lg-1', 'busy_max': 16.3, 'busy_mean': 15.8, 'late_max_us': 1390, 'conn_opens': 1121, 'max_inflight': 1116}, {'vm': 'rv-pbf-lg-2', 'busy_max': 16.1, 'busy_mean': 15.5, 'late_max_us': 247, 'conn_opens': 1123, 'max_inflight': 1116}, {'vm': 'rv-pbf-lg-3', 'busy_max': 16.2, 'busy_mean': 15.6, 'late_max_us': 318, 'conn_opens': 1124, 'max_inflight': 1115}]  provider_cpu_busy_max: 19.847037198402163
gateway cores total 24.17 cpu-ms/req {'gateway': 34.649, 'workers': 30.668, 'owners': 3.979}
  rv-pbf-unit-2: cores {'launcher': 0.0, 'owner': 0.692, 'owner0': 0.345, 'owner1': 0.347, 'redis': 0.001, 'worker': 5.283} worker util max 0.33 per-core max 0.272 mean 0.253 gpu {'0': {'n': 298, 'sm_mean': 26.7, 'sm_p95': 38.0, 'sm_max': 54.0}, '1': {'n': 298, 'sm_mean': 26.0, 'sm_p95': 37.0, 'sm_max': 48.0}} t_input_p99 15.532 per-worker admitted {'n': 18, 'min': 1971, 'max': 3404, 'mean': 2914.6, 'max_over_mean': 1.168, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
  rv-pbf-unit-3: cores {'launcher': 0.0, 'owner': 0.7, 'owner0': 0.355, 'owner1': 0.345, 'redis': 0.001, 'worker': 5.52} worker util max 0.351 per-core max 0.282 mean 0.262 gpu {'0': {'n': 298, 'sm_mean': 26.5, 'sm_p95': 38.0, 'sm_max': 48.0}, '1': {'n': 298, 'sm_mean': 25.5, 'sm_p95': 38.0, 'sm_max': 42.0}} t_input_p99 15.7942 per-worker admitted {'n': 18, 'min': 2389, 'max': 3424, 'mean': 2911.3, 'max_over_mean': 1.176, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
  rv-pbf-unit-4: cores {'launcher': 0.0, 'owner': 0.692, 'owner0': 0.354, 'owner1': 0.338, 'redis': 0.001, 'worker': 5.179} worker util max 0.362 per-core max 0.266 mean 0.248 gpu {'0': {'n': 298, 'sm_mean': 27.0, 'sm_p95': 40.0, 'sm_max': 46.0}, '1': {'n': 298, 'sm_mean': 25.6, 'sm_p95': 39.0, 'sm_max': 48.0}} t_input_p99 15.1388 per-worker admitted {'n': 18, 'min': 1910, 'max': 3789, 'mean': 2912.8, 'max_over_mean': 1.301, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
  rv-pbf-unit-5: cores {'launcher': 0.0, 'owner': 0.692, 'owner0': 0.339, 'owner1': 0.353, 'redis': 0.001, 'worker': 5.414} worker util max 0.357 per-core max 0.279 mean 0.258 gpu {'0': {'n': 298, 'sm_mean': 25.6, 'sm_p95': 37.0, 'sm_max': 49.0}, '1': {'n': 298, 'sm_mean': 27.0, 'sm_p95': 39.0, 'sm_max': 49.0}} t_input_p99 15.2699 per-worker admitted {'n': 18, 'min': 2135, 'max': 3587, 'mean': 2911.8, 'max_over_mean': 1.232, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
W t_input_ns: {'n': 209714, 'mean_ms': 8.4412, 'p50_ms': 8.9784, 'p90_ms': 11.862, 'p99_ms': 15.401, 'p99.9_ms': 20.8404, 'max_cum_ms': 49.401}
W t_tokenize_ns: {'n': 209711, 'mean_ms': 3.2339, 'p50_ms': 3.2276, 'p90_ms': 5.1446, 'p99_ms': 6.0621, 'p99.9_ms': 6.783, 'max_cum_ms': 8.1067}
W t_guard_wait_ns: {'n': 209714, 'mean_ms': 4.5857, 'p50_ms': 4.8824, 'p90_ms': 6.9796, 'p99_ms': 10.2892, 'p99.9_ms': 15.1388, 'max_cum_ms': 46.4385}
W guard_owner_rtt_ns: {'n': 209714, 'mean_ms': 4.7606, 'p50_ms': 5.079, 'p90_ms': 7.2417, 'p99_ms': 9.8959, 'p99.9_ms': 13.9592, 'max_cum_ms': 45.1939}
W guard_queue_ns: {'n': 209708, 'mean_ms': 0.3601, 'p50_ms': 0.105, 'p90_ms': 0.9871, 'p99_ms': 4.2926, 'p99.9_ms': 6.0621, 'max_cum_ms': 11.4231}
W guard_exec_ns: {'n': 209708, 'mean_ms': 3.5545, 'p50_ms': 4.2271, 'p90_ms': 4.4892, 'p99_ms': 6.5864, 'p99.9_ms': 6.783, 'max_cum_ms': 9.4796}
W t_admit_ns: {'n': 209710, 'mean_ms': 0.0949, 'p50_ms': 0.0886, 'p90_ms': 0.1132, 'p99_ms': 0.1464, 'p99.9_ms': 0.9953, 'max_cum_ms': 32.41}
W release_processing_ns: {'n': 32319649, 'mean_ms': 0.0647, 'p50_ms': 0.0632, 'p90_ms': 0.0855, 'p99_ms': 0.1142, 'p99.9_ms': 0.1444, 'max_cum_ms': 34.5668}
W loop_lag_ns: {'n': 214110, 'mean_ms': 0.9301, 'p50_ms': 0.0147, 'p90_ms': 3.8502, 'p99_ms': 10.1581, 'p99.9_ms': 12.9106, 'max_cum_ms': 38.8089}
W audit_batch_write_ns: {'n': 415008, 'mean_ms': 1.169, 'p50_ms': 0.9789, 'p90_ms': 1.3353, 'p99_ms': 6.3898, 'p99.9_ms': 12.1242, 'max_cum_ms': 49.1949}
W counts: {"admitted": 209710, "audit_enqueued": 416076, "audit_written": 416078, "background_round_trips": 42822, "disposition_ALLOW": 206121, "disposition_BLOCK": 3593, "guard_deadline_expired": 6, "guard_unavailable_findings": 6, "guard_windows": 343672, "lease_refills": 962, "provider_calls": 206121, "provider_connections_opened": 48602, "requests_by_round_trips{n=\"0\"}": 208748, "requests_by_round_trips{n=\"1\"}": 962, "shared_state_round_trips": 962}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 1.361}, "per_proc_util": {"nginx": [0.0, 0.079, 0.08, 0.082, 0.082, 0.083, 0.084, 0.085, 0.085, 0.086, 0.086, 0.086, 0.086, 0.086, 0.089, 0.09, 0.091]}, "per_core_util": {"max": 0.088, "mean": 0.086, "sum": 1.37, "n": 16}, "nic": null, "restarted": [], "loadavg_max": 1.44} nginx cpu-ms/req 1.951
redis: ops/s 1990.4 ops/req 2.843 cpu cores 0.039 clients 361 mem 2644.8MB ping(us) {'n': 28412, 'p50_us': 441.6, 'p90_us': 470.3, 'p99_us': 545.4, 'p99.9_us': 2592.7, 'max_us': 5045.6, 'mean_us': 454.1}
redis cmdstats: {'ping': {'calls_per_s': 94.5, 'usec_per_call': 0.14}, 'hgetall': {'calls_per_s': 356.9, 'usec_per_call': 0.27}, 'decrby': {'calls_per_s': 3.2, 'usec_per_call': 0.35}, 'mget': {'calls_per_s': 142.7, 'usec_per_call': 0.46}, 'xadd': {'calls_per_s': 1386.6, 'usec_per_call': 5.87}, 'info': {'calls_per_s': 0.0, 'usec_per_call': 133.0}, 'get': {'calls_per_s': 3.2, 'usec_per_call': 0.53}, 'evalsha': {'calls_per_s': 3.2, 'usec_per_call': 10.49}}
wire: {'requests_in_window': 210000, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56579.2, 'client_side_to_unit': 9049.7, 'unit_to_provider': 9732.1, 'provider_to_unit': 57276.3, 'unit_to_redis': 5433.8, 'redis_to_unit': 285.3}, 'edge_ip_bytes_per_req': {'edge_to_clients': 56689.7, 'clients_to_edge': 7861.9}, 'olg_resp_body_bytes_mean': {'sse': 67904.2, 'json': 1422.4}}
