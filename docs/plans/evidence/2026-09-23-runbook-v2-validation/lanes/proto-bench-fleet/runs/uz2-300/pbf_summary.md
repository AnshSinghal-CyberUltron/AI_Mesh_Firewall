# uz2-300: strict FAIL | load-knee PASS (sut, units=2, rate=300)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 90000 (300.0/s) qualified 88418 (294.73/s) FP-blocks 1581 (0.01757) infra 1 (1.1111111111111112e-05) drops 0 safety 0
infra reasons: {'block_on_unavailable_sem': 1}
infra detail: {'403 http_403 type=policy_violation code=blocked_by_policy': 1}

T_fw_addon: n=88418 p50=47.7522 p90=55.2757 p99=72.3569 p99.9=80.3568 max=107.6839 mean=38.8214
T_fw_addon_nohold: n=88418 p50=11.0465 p90=14.5659 p99=19.0347 p99.9=24.5076 max=60.2627 mean=10.6911
T_fw_addon_sse: n=61909 p50=50.8359 p90=56.5507 p99=73.0908 p99.9=86.5047 max=107.6839 mean=50.8301
T_fw_addon_json: n=26509 p50=11.1108 p90=14.676 p99=19.408 p99.9=25.4056 max=53.7214 mean=10.7763
T_addon_first_sse: n=61909 p50=10.9605 p90=14.7325 p99=29.6925 p99.9=36.6555 max=67.6952 mean=10.8736
T_addon_total_sse: n=61909 p50=11.0224 p90=14.5182 p99=18.8526 p99.9=24.1997 max=60.2627 mean=10.6546
T_addon_total_json: n=26509 p50=11.1108 p90=14.676 p99=19.408 p99.9=25.4056 max=53.7214 mean=10.7763
T_release_lag_max: n=61909 p50=50.8359 p90=56.5507 p99=73.0908 p99.9=86.5047 max=107.6839 mean=50.8301
lateness: n=90000 p50=0.0872 p90=0.0963 p99=0.107 p99.9=0.1326 max=0.3751 mean=0.0871
loadgen: [{'vm': 'rv-pbf-lg-1', 'busy_max': 8.2, 'busy_mean': 7.7, 'late_max_us': 718, 'conn_opens': 519, 'max_inflight': 497}, {'vm': 'rv-pbf-lg-2', 'busy_max': 8.2, 'busy_mean': 7.6, 'late_max_us': 261, 'conn_opens': 522, 'max_inflight': 497}, {'vm': 'rv-pbf-lg-3', 'busy_max': 8.2, 'busy_mean': 7.8, 'late_max_us': 375, 'conn_opens': 519, 'max_inflight': 497}]  provider_cpu_busy_max: 13.275938952253785
gateway cores total 10.31 cpu-ms/req {'gateway': 34.472, 'workers': 30.49, 'owners': 3.978}
  rv-pbf-unit-2: cores {'launcher': 0.0, 'owner': 0.591, 'owner0': 0.301, 'owner1': 0.29, 'redis': 0.001, 'worker': 4.43} worker util max 0.3 per-core max 0.232 mean 0.212 gpu {'0': {'n': 298, 'sm_mean': 23.0, 'sm_p95': 36.0, 'sm_max': 40.0}, '1': {'n': 298, 'sm_mean': 21.9, 'sm_p95': 33.0, 'sm_max': 42.0}} t_input_p99 14.0902 per-worker admitted {'n': 18, 'min': 1800, 'max': 3203, 'mean': 2496.9, 'max_over_mean': 1.283, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
  rv-pbf-unit-3: cores {'launcher': 0.0, 'owner': 0.599, 'owner0': 0.29, 'owner1': 0.308, 'redis': 0.001, 'worker': 4.687} worker util max 0.335 per-core max 0.244 mean 0.223 gpu {'0': {'n': 299, 'sm_mean': 21.9, 'sm_p95': 33.0, 'sm_max': 42.0}, '1': {'n': 299, 'sm_mean': 22.8, 'sm_p95': 35.0, 'sm_max': 48.0}} t_input_p99 14.3524 per-worker admitted {'n': 18, 'min': 1743, 'max': 3376, 'mean': 2498.7, 'max_over_mean': 1.351, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
W t_input_ns: {'n': 89922, 'mean_ms': 8.1858, 'p50_ms': 8.8474, 'p90_ms': 11.4688, 'p99_ms': 14.2213, 'p99.9_ms': 19.5297, 'max_cum_ms': 47.9697}
W t_tokenize_ns: {'n': 89922, 'mean_ms': 3.2294, 'p50_ms': 3.2604, 'p90_ms': 5.079, 'p99_ms': 5.7999, 'p99.9_ms': 6.5864, 'max_cum_ms': 7.9125}
W t_guard_wait_ns: {'n': 89922, 'mean_ms': 4.3673, 'p50_ms': 4.8169, 'p90_ms': 6.783, 'p99_ms': 8.8474, 'p99.9_ms': 14.3524, 'max_cum_ms': 46.4385}
W guard_owner_rtt_ns: {'n': 89922, 'mean_ms': 4.5376, 'p50_ms': 5.0135, 'p90_ms': 6.914, 'p99_ms': 8.7163, 'p99.9_ms': 13.3038, 'max_cum_ms': 45.1939}
W guard_queue_ns: {'n': 89921, 'mean_ms': 0.1982, 'p50_ms': 0.0998, 'p90_ms': 0.1423, 'p99_ms': 2.8344, 'p99.9_ms': 4.7514, 'max_cum_ms': 11.4164}
W guard_exec_ns: {'n': 89921, 'mean_ms': 3.5525, 'p50_ms': 4.2271, 'p90_ms': 4.4892, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 9.4796}
W t_admit_ns: {'n': 89922, 'mean_ms': 0.0932, 'p50_ms': 0.0865, 'p90_ms': 0.1111, 'p99_ms': 0.1444, 'p99.9_ms': 1.0035, 'max_cum_ms': 6.9353}
W release_processing_ns: {'n': 13812726, 'mean_ms': 0.064, 'p50_ms': 0.0627, 'p90_ms': 0.0845, 'p99_ms': 0.1132, 'p99.9_ms': 0.1444, 'max_cum_ms': 31.4297}
W loop_lag_ns: {'n': 107170, 'mean_ms': 0.8869, 'p50_ms': 0.0121, 'p90_ms': 3.6864, 'p99_ms': 9.5027, 'p99.9_ms': 12.2552, 'max_cum_ms': 34.298}
W audit_batch_write_ns: {'n': 178074, 'mean_ms': 1.0746, 'p50_ms': 0.9626, 'p90_ms': 1.2861, 'p99_ms': 4.6203, 'p99.9_ms': 10.2892, 'max_cum_ms': 49.1949}
W counts: {"admitted": 89922, "audit_enqueued": 178405, "audit_written": 178404, "background_round_trips": 21434, "disposition_ALLOW": 88340, "disposition_BLOCK": 1582, "guard_deadline_expired": 1, "guard_unavailable_findings": 1, "guard_windows": 147642, "lease_refills": 410, "provider_calls": 88340, "provider_connections_opened": 16934, "requests_by_round_trips{n=\"0\"}": 89512, "requests_by_round_trips{n=\"1\"}": 410, "shared_state_round_trips": 410}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 0.479}, "per_proc_util": {"nginx": [0.0, 0.026, 0.027, 0.027, 0.027, 0.028, 0.028, 0.029, 0.029, 0.031, 0.031, 0.031, 0.032, 0.032, 0.032, 0.033, 0.035]}, "per_core_util": {"max": 0.031, "mean": 0.03, "sum": 0.48, "n": 16}, "nic": null, "restarted": [], "loadavg_max": 0.95} nginx cpu-ms/req 1.603
redis: ops/s 1193.6 ops/req 3.979 cpu cores 0.016 clients 289 mem 1991.0MB ping(us) {'n': 28251, 'p50_us': 505.7, 'p90_us': 534.8, 'p99_us': 635.9, 'p99.9_us': 2431.8, 'max_us': 4317.9, 'mean_us': 517.7}
redis cmdstats: {'ping': {'calls_per_s': 94.0, 'usec_per_call': 0.11}, 'hgetall': {'calls_per_s': 358.2, 'usec_per_call': 0.22}, 'decrby': {'calls_per_s': 1.4, 'usec_per_call': 0.39}, 'mget': {'calls_per_s': 143.3, 'usec_per_call': 0.38}, 'xadd': {'calls_per_s': 594.0, 'usec_per_call': 4.3}, 'info': {'calls_per_s': 0.0, 'usec_per_call': 116.0}, 'get': {'calls_per_s': 1.4, 'usec_per_call': 0.58}, 'evalsha': {'calls_per_s': 1.4, 'usec_per_call': 11.1}}
wire: {'requests_in_window': 90000, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56385.1, 'client_side_to_unit': 8656.5, 'unit_to_provider': 9410.1, 'provider_to_unit': 57077.3, 'unit_to_redis': 5458.1, 'redis_to_unit': 300.6}, 'edge_ip_bytes_per_req': {'edge_to_clients': 56501.3, 'clients_to_edge': 8016.9}, 'olg_resp_body_bytes_mean': {'sse': 67698.4, 'json': 1419.2}}
