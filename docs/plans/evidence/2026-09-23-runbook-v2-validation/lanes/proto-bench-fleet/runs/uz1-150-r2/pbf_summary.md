# uz1-150-r2: strict FAIL | load-knee PASS (sut, units=1, rate=150)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 45000 (150.0/s) qualified 44216 (147.39/s) FP-blocks 784 (0.01742) infra 0 (0.0) drops 0 safety 0
infra reasons: {}
infra detail: {}

T_fw_addon: n=44216 p50=47.5533 p90=55.0367 p99=72.329 p99.9=81.5477 max=95.2594 mean=38.5961
T_fw_addon_nohold: n=44216 p50=10.912 p90=14.3645 p99=18.3672 p99.9=23.9285 max=35.6506 mean=10.513
T_fw_addon_sse: n=30983 p50=50.6791 p90=56.2465 p99=73.0559 p99.9=86.6573 max=95.2594 mean=50.5623
T_fw_addon_json: n=13233 p50=10.9318 p90=14.426 p99=18.4543 p99.9=24.1979 max=35.6506 mean=10.5793
T_addon_first_sse: n=30983 p50=10.8548 p90=14.6158 p99=30.3879 p99.9=35.4872 max=57.4077 mean=10.7293
T_addon_total_sse: n=30983 p50=10.907 p90=14.3286 p99=18.3464 p99.9=23.8266 max=30.4384 mean=10.4847
T_addon_total_json: n=13233 p50=10.9318 p90=14.426 p99=18.4543 p99.9=24.1979 max=35.6506 mean=10.5793
T_release_lag_max: n=30983 p50=50.6791 p90=56.2465 p99=73.0559 p99.9=86.6573 max=95.2594 mean=50.5623
lateness: n=45000 p50=0.0881 p90=0.0987 p99=0.111 p99.9=0.1272 max=0.2632 mean=0.0883
loadgen: [{'vm': 'rv-pbf-lg-1', 'busy_max': 6.0, 'busy_mean': 5.2, 'late_max_us': 427, 'conn_opens': 278, 'max_inflight': 259}, {'vm': 'rv-pbf-lg-2', 'busy_max': 18.4, 'busy_mean': 5.4, 'late_max_us': 263, 'conn_opens': 278, 'max_inflight': 259}, {'vm': 'rv-pbf-lg-3', 'busy_max': 19.5, 'busy_mean': 5.5, 'late_max_us': 206, 'conn_opens': 278, 'max_inflight': 259}]  provider_cpu_busy_max: 21.38163812127797
gateway cores total 5.04 cpu-ms/req {'gateway': 33.741, 'workers': 29.779, 'owners': 3.959}
  rv-pbf-unit-2: cores {'launcher': 0.0, 'owner': 0.592, 'owner0': 0.302, 'owner1': 0.29, 'redis': 0.001, 'worker': 4.452} worker util max 0.312 per-core max 0.229 mean 0.214 gpu {'0': {'n': 298, 'sm_mean': 23.3, 'sm_p95': 35.0, 'sm_max': 45.0}, '1': {'n': 298, 'sm_mean': 22.6, 'sm_p95': 35.0, 'sm_max': 46.0}} t_input_p99 14.0902 per-worker admitted {'n': 18, 'min': 1834, 'max': 3252, 'mean': 2497.6, 'max_over_mean': 1.302, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
W t_input_ns: {'n': 44957, 'mean_ms': 8.0354, 'p50_ms': 8.7163, 'p90_ms': 11.2067, 'p99_ms': 14.0902, 'p99.9_ms': 19.2676, 'max_cum_ms': 28.6608}
W t_tokenize_ns: {'n': 44956, 'mean_ms': 3.1794, 'p50_ms': 3.2276, 'p90_ms': 4.948, 'p99_ms': 5.6689, 'p99.9_ms': 6.5864, 'max_cum_ms': 7.5832}
W t_guard_wait_ns: {'n': 44957, 'mean_ms': 4.2783, 'p50_ms': 4.7514, 'p90_ms': 6.4553, 'p99_ms': 8.4541, 'p99.9_ms': 13.697, 'max_cum_ms': 18.1739}
W guard_owner_rtt_ns: {'n': 44957, 'mean_ms': 4.4494, 'p50_ms': 4.948, 'p90_ms': 6.5208, 'p99_ms': 8.4541, 'p99.9_ms': 12.7795, 'max_cum_ms': 16.6495}
W guard_queue_ns: {'n': 44957, 'mean_ms': 0.15, 'p50_ms': 0.0957, 'p90_ms': 0.1285, 'p99_ms': 1.8104, 'p99.9_ms': 4.3581, 'max_cum_ms': 7.375}
W guard_exec_ns: {'n': 44957, 'mean_ms': 3.5461, 'p50_ms': 4.2271, 'p90_ms': 4.4237, 'p99_ms': 6.5208, 'p99.9_ms': 6.6519, 'max_cum_ms': 9.0848}
W t_admit_ns: {'n': 44956, 'mean_ms': 0.0933, 'p50_ms': 0.0865, 'p90_ms': 0.1111, 'p99_ms': 0.1423, 'p99.9_ms': 1.0895, 'max_cum_ms': 7.5772}
W release_processing_ns: {'n': 6903965, 'mean_ms': 0.0631, 'p50_ms': 0.0622, 'p90_ms': 0.0835, 'p99_ms': 0.1101, 'p99.9_ms': 0.1403, 'max_cum_ms': 2.9731}
W loop_lag_ns: {'n': 53580, 'mean_ms': 0.8267, 'p50_ms': 0.0109, 'p90_ms': 3.3587, 'p99_ms': 8.8474, 'p99.9_ms': 11.862, 'max_cum_ms': 22.5598}
W audit_batch_write_ns: {'n': 89006, 'mean_ms': 1.1366, 'p50_ms': 1.0199, 'p90_ms': 1.3681, 'p99_ms': 4.7514, 'p99.9_ms': 9.7649, 'max_cum_ms': 23.1609}
W counts: {"admitted": 44956, "audit_enqueued": 89169, "audit_written": 89170, "background_round_trips": 10716, "disposition_ALLOW": 44174, "disposition_BLOCK": 783, "guard_windows": 74088, "lease_refills": 205, "provider_calls": 44174, "provider_connections_opened": 8891, "requests_by_round_trips{n=\"0\"}": 44751, "requests_by_round_trips{n=\"1\"}": 205, "shared_state_round_trips": 205}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 0.198}, "per_proc_util": {"nginx": [0.0, 0.009, 0.01, 0.01, 0.01, 0.011, 0.012, 0.012, 0.012, 0.012, 0.012, 0.013, 0.013, 0.013, 0.014, 0.015, 0.017]}, "per_core_util": {"max": 0.015, "mean": 0.013, "sum": 0.21, "n": 16}, "nic": null, "restarted": [], "loadavg_max": 0.37} nginx cpu-ms/req 1.323
redis: ops/s 517.7 ops/req 3.451 cpu cores 0.006 clients 91 mem 309.4MB ping(us) {'n': 28112, 'p50_us': 557.6, 'p90_us': 635.6, 'p99_us': 718.6, 'p99.9_us': 1904.1, 'max_us': 4605.3, 'mean_us': 573.4}
redis cmdstats: {'xadd': {'calls_per_s': 297.1, 'usec_per_call': 3.33}, 'hgetall': {'calls_per_s': 89.3, 'usec_per_call': 0.23}, 'mget': {'calls_per_s': 35.7, 'usec_per_call': 0.41}, 'evalsha': {'calls_per_s': 0.7, 'usec_per_call': 10.53}, 'ping': {'calls_per_s': 93.5, 'usec_per_call': 0.1}, 'get': {'calls_per_s': 0.7, 'usec_per_call': 0.55}, 'decrby': {'calls_per_s': 0.7, 'usec_per_call': 0.33}, 'info': {'calls_per_s': 0.0, 'usec_per_call': 126.0}}
wire: {'requests_in_window': 45000, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56394.8, 'client_side_to_unit': 8059.8, 'unit_to_provider': 8890.6, 'provider_to_unit': 57086.1, 'unit_to_redis': 5473.8, 'redis_to_unit': 318.9}, 'edge_ip_bytes_per_req': {'edge_to_clients': 56515.9, 'clients_to_edge': 8186.3}, 'olg_resp_body_bytes_mean': {'sse': 67653.6, 'json': 1426.5}}
