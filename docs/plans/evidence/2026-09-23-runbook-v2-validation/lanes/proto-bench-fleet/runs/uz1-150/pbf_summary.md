# uz1-150: strict FAIL | load-knee PASS (sut, units=1, rate=150)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 45000 (150.0/s) qualified 44185 (147.28/s) FP-blocks 815 (0.01811) infra 0 (0.0) drops 0 safety 0
infra reasons: {}
infra detail: {}

T_fw_addon: n=44185 p50=47.5945 p90=55.0664 p99=72.3141 p99.9=85.7089 max=133.8806 mean=38.672
T_fw_addon_nohold: n=44185 p50=10.9509 p90=14.4781 p99=18.3348 p99.9=23.7054 max=33.2312 mean=10.5491
T_fw_addon_sse: n=30960 p50=50.7179 p90=56.3102 p99=73.0491 p99.9=87.477 max=133.8806 mean=50.6468
T_fw_addon_json: n=13225 p50=10.9919 p90=14.6799 p99=18.7773 p99.9=23.9194 max=29.7792 mean=10.6386
T_addon_first_sse: n=30960 p50=10.8843 p90=14.6352 p99=28.7976 p99.9=35.4849 max=53.62 mean=10.7359
T_addon_total_sse: n=30960 p50=10.9361 p90=14.3762 p99=18.206 p99.9=23.7054 max=33.2312 mean=10.5109
T_addon_total_json: n=13225 p50=10.9919 p90=14.6799 p99=18.7773 p99.9=23.9194 max=29.7792 mean=10.6386
T_release_lag_max: n=30960 p50=50.7179 p90=56.3102 p99=73.0491 p99.9=87.477 max=133.8806 mean=50.6468
lateness: n=45000 p50=0.0876 p90=0.0982 p99=0.11 p99.9=0.1238 max=0.2999 mean=0.0874
loadgen: [{'vm': 'rv-pbf-lg-1', 'busy_max': 18.8, 'busy_mean': 5.3, 'late_max_us': 465, 'conn_opens': 277, 'max_inflight': 259}, {'vm': 'rv-pbf-lg-2', 'busy_max': 18.0, 'busy_mean': 5.2, 'late_max_us': 181, 'conn_opens': 277, 'max_inflight': 259}, {'vm': 'rv-pbf-lg-3', 'busy_max': 16.8, 'busy_mean': 5.2, 'late_max_us': 337, 'conn_opens': 278, 'max_inflight': 259}]  provider_cpu_busy_max: 22.13006827154228
gateway cores total 5.11 cpu-ms/req {'gateway': 34.191, 'workers': 30.221, 'owners': 3.967}
  rv-pbf-unit-2: cores {'launcher': 0.0, 'owner': 0.593, 'owner0': 0.306, 'owner1': 0.287, 'redis': 0.001, 'worker': 4.518} worker util max 0.311 per-core max 0.235 mean 0.217 gpu {'0': {'n': 298, 'sm_mean': 23.7, 'sm_p95': 37.0, 'sm_max': 44.0}, '1': {'n': 298, 'sm_mean': 21.9, 'sm_p95': 35.0, 'sm_max': 42.0}} t_input_p99 14.0902 per-worker admitted {'n': 18, 'min': 1786, 'max': 3298, 'mean': 2496.8, 'max_over_mean': 1.321, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
W t_input_ns: {'n': 44943, 'mean_ms': 8.0806, 'p50_ms': 8.7163, 'p90_ms': 11.3377, 'p99_ms': 14.0902, 'p99.9_ms': 19.2676, 'max_cum_ms': 28.1758}
W t_tokenize_ns: {'n': 44943, 'mean_ms': 3.1912, 'p50_ms': 3.2276, 'p90_ms': 4.948, 'p99_ms': 5.7344, 'p99.9_ms': 6.5864, 'max_cum_ms': 7.5126}
W t_guard_wait_ns: {'n': 44943, 'mean_ms': 4.3063, 'p50_ms': 4.8169, 'p90_ms': 6.6519, 'p99_ms': 8.5852, 'p99.9_ms': 13.9592, 'max_cum_ms': 19.376}
W guard_owner_rtt_ns: {'n': 44943, 'mean_ms': 4.4751, 'p50_ms': 5.0135, 'p90_ms': 6.7174, 'p99_ms': 8.5852, 'p99.9_ms': 12.9106, 'max_cum_ms': 17.365}
W guard_queue_ns: {'n': 44943, 'mean_ms': 0.1529, 'p50_ms': 0.0968, 'p90_ms': 0.1306, 'p99_ms': 1.9087, 'p99.9_ms': 4.4237, 'max_cum_ms': 8.8115}
W guard_exec_ns: {'n': 44943, 'mean_ms': 3.5507, 'p50_ms': 4.2271, 'p90_ms': 4.4237, 'p99_ms': 6.5208, 'p99.9_ms': 6.7174, 'max_cum_ms': 9.0647}
W t_admit_ns: {'n': 44943, 'mean_ms': 0.0942, 'p50_ms': 0.0876, 'p90_ms': 0.1121, 'p99_ms': 0.1444, 'p99.9_ms': 1.0568, 'max_cum_ms': 6.9353}
W release_processing_ns: {'n': 6906988, 'mean_ms': 0.0633, 'p50_ms': 0.0622, 'p90_ms': 0.0845, 'p99_ms': 0.1121, 'p99.9_ms': 0.1423, 'max_cum_ms': 2.6852}
W loop_lag_ns: {'n': 53640, 'mean_ms': 0.832, 'p50_ms': 0.0116, 'p90_ms': 3.457, 'p99_ms': 8.9784, 'p99.9_ms': 11.862, 'max_cum_ms': 20.7206}
W audit_batch_write_ns: {'n': 88979, 'mean_ms': 1.1149, 'p50_ms': 1.0035, 'p90_ms': 1.3353, 'p99_ms': 4.6858, 'p99.9_ms': 10.027, 'max_cum_ms': 20.2899}
W counts: {"admitted": 44943, "audit_enqueued": 89157, "audit_written": 89157, "background_round_trips": 10728, "disposition_ALLOW": 44130, "disposition_BLOCK": 813, "guard_windows": 74064, "lease_refills": 208, "provider_calls": 44130, "provider_connections_opened": 8296, "requests_by_round_trips{n=\"0\"}": 44735, "requests_by_round_trips{n=\"1\"}": 208, "shared_state_round_trips": 208}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 0.201}, "per_proc_util": {"nginx": [0.0, 0.009, 0.01, 0.011, 0.011, 0.012, 0.012, 0.012, 0.013, 0.013, 0.013, 0.014, 0.014, 0.014, 0.014, 0.015, 0.017]}, "per_core_util": {"max": 0.014, "mean": 0.013, "sum": 0.21, "n": 16}, "nic": null, "restarted": [], "loadavg_max": 0.45} nginx cpu-ms/req 1.347
redis: ops/s 895.5 ops/req 5.97 cpu cores 0.009 clients 253 mem 310.9MB ping(us) {'n': 28288, 'p50_us': 497.3, 'p90_us': 525.8, 'p99_us': 619.4, 'p99.9_us': 1931.7, 'max_us': 4827.7, 'mean_us': 505.6}
redis cmdstats: {'ping': {'calls_per_s': 94.1, 'usec_per_call': 0.09}, 'hgetall': {'calls_per_s': 358.8, 'usec_per_call': 0.2}, 'decrby': {'calls_per_s': 0.7, 'usec_per_call': 0.4}, 'mget': {'calls_per_s': 143.5, 'usec_per_call': 0.36}, 'xadd': {'calls_per_s': 297.0, 'usec_per_call': 3.64}, 'info': {'calls_per_s': 0.0, 'usec_per_call': 120.0}, 'get': {'calls_per_s': 0.7, 'usec_per_call': 0.53}, 'evalsha': {'calls_per_s': 0.7, 'usec_per_call': 10.93}}
wire: {'requests_in_window': 45000, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56356.8, 'client_side_to_unit': 8067.0, 'unit_to_provider': 8845.8, 'provider_to_unit': 57045.2, 'unit_to_redis': 5473.0, 'redis_to_unit': 319.1}, 'edge_ip_bytes_per_req': {'edge_to_clients': 56483.9, 'clients_to_edge': 8219.6}, 'olg_resp_body_bytes_mean': {'sse': 67666.3, 'json': 1426.6}}
