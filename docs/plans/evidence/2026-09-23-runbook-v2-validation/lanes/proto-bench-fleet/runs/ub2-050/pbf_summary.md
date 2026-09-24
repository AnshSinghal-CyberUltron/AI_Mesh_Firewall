# ub2-050: strict FAIL | load-knee PASS (sut, units=2, rate=50)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 15000 (50.0/s) qualified 14810 (49.37/s) FP-blocks 190 (0.01267) infra 0 (0.0) drops 0 safety 0
infra reasons: {}
infra detail: {}

T_fw_addon: n=14810 p50=46.984 p90=53.1057 p99=70.9575 p99.9=74.8647 max=90.9459 mean=37.3349
T_fw_addon_nohold: n=14810 p50=10.143 p90=12.8226 p99=15.3022 p99.9=18.9254 max=25.1416 mean=9.6281
T_fw_addon_sse: n=10363 p50=49.8077 p90=53.9453 p99=71.3906 p99.9=75.9244 max=90.9459 mean=49.1888
T_fw_addon_json: n=4447 p50=10.2073 p90=13.0209 p99=15.3485 p99.9=19.3893 max=25.1416 mean=9.7115
T_addon_first_sse: n=10363 p50=10.0519 p90=13.234 p99=28.5458 p99.9=33.2121 max=59.1634 mean=9.8276
T_addon_total_sse: n=10363 p50=10.1118 p90=12.776 p99=15.2602 p99.9=17.7523 max=21.2369 mean=9.5923
T_addon_total_json: n=4447 p50=10.2073 p90=13.0209 p99=15.3485 p99.9=19.3893 max=25.1416 mean=9.7115
T_release_lag_max: n=10363 p50=49.8077 p90=53.9453 p99=71.3906 p99.9=75.9244 max=90.9459 mean=49.1888
lateness: n=15000 p50=0.0928 p90=0.1127 p99=0.131 p99.9=0.1539 max=0.2218 mean=0.0952
loadgen: [{'vm': 'rv-pbf-lg-1', 'busy_max': 4.1, 'busy_mean': 3.8, 'late_max_us': 259, 'conn_opens': 98, 'max_inflight': 93}, {'vm': 'rv-pbf-lg-2', 'busy_max': 4.2, 'busy_mean': 3.9, 'late_max_us': 437, 'conn_opens': 98, 'max_inflight': 93}, {'vm': 'rv-pbf-lg-3', 'busy_max': 4.2, 'busy_mean': 3.9, 'late_max_us': 213, 'conn_opens': 97, 'max_inflight': 93}]  provider_cpu_busy_max: 5.016243780614338
gateway cores total 1.91 cpu-ms/req {'gateway': 38.264, 'workers': 34.146, 'owners': 4.101}
  rv-pbf-unit-2: cores {'launcher': 0.0, 'owner': 0.101, 'owner0': 0.045, 'owner1': 0.056, 'redis': 0.001, 'worker': 0.838} worker util max 0.074 per-core max 0.06 mean 0.041 gpu {'0': {'n': 298, 'sm_mean': 3.4, 'sm_p95': 9.0, 'sm_max': 15.0}, '1': {'n': 298, 'sm_mean': 4.2, 'sm_p95': 10.0, 'sm_max': 16.0}} t_input_p99 12.1242 per-worker admitted {'n': 18, 'min': 202, 'max': 758, 'mean': 416.2, 'max_over_mean': 1.821, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
  rv-pbf-unit-3: cores {'launcher': 0.0, 'owner': 0.103, 'owner0': 0.061, 'owner1': 0.042, 'redis': 0.001, 'worker': 0.864} worker util max 0.067 per-core max 0.058 mean 0.042 gpu {'0': {'n': 298, 'sm_mean': 4.5, 'sm_p95': 9.0, 'sm_max': 15.0}, '1': {'n': 298, 'sm_mean': 2.9, 'sm_p95': 7.0, 'sm_max': 13.0}} t_input_p99 12.3863 per-worker admitted {'n': 18, 'min': 110, 'max': 646, 'mean': 416.5, 'max_over_mean': 1.551, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
W t_input_ns: {'n': 14988, 'mean_ms': 7.3619, 'p50_ms': 8.0937, 'p90_ms': 9.7649, 'p99_ms': 12.2552, 'p99.9_ms': 15.1388, 'max_cum_ms': 29.3969}
W t_tokenize_ns: {'n': 14988, 'mean_ms': 2.6688, 'p50_ms': 2.7034, 'p90_ms': 4.0468, 'p99_ms': 4.6203, 'p99.9_ms': 5.7344, 'max_cum_ms': 7.1509}
W t_guard_wait_ns: {'n': 14988, 'mean_ms': 4.1636, 'p50_ms': 4.6858, 'p90_ms': 5.1446, 'p99_ms': 7.2417, 'p99.9_ms': 10.8134, 'max_cum_ms': 15.6537}
W guard_owner_rtt_ns: {'n': 14988, 'mean_ms': 4.3281, 'p50_ms': 4.8824, 'p90_ms': 5.3412, 'p99_ms': 7.4383, 'p99.9_ms': 9.5027, 'max_cum_ms': 17.0599}
W guard_queue_ns: {'n': 14988, 'mean_ms': 0.1054, 'p50_ms': 0.105, 'p90_ms': 0.1234, 'p99_ms': 0.1444, 'p99.9_ms': 0.2079, 'max_cum_ms': 1.0339}
W guard_exec_ns: {'n': 14988, 'mean_ms': 3.5727, 'p50_ms': 4.2271, 'p90_ms': 4.4892, 'p99_ms': 6.5208, 'p99.9_ms': 6.7174, 'max_cum_ms': 9.296}
W t_admit_ns: {'n': 14988, 'mean_ms': 0.0946, 'p50_ms': 0.0876, 'p90_ms': 0.1101, 'p99_ms': 0.1403, 'p99.9_ms': 1.0732, 'max_cum_ms': 7.6442}
W release_processing_ns: {'n': 2299123, 'mean_ms': 0.0633, 'p50_ms': 0.0627, 'p90_ms': 0.0814, 'p99_ms': 0.106, 'p99.9_ms': 0.1382, 'max_cum_ms': 1.4361}
W loop_lag_ns: {'n': 107310, 'mean_ms': 0.6561, 'p50_ms': 0.018, 'p90_ms': 1.1223, 'p99_ms': 6.3898, 'p99.9_ms': 7.8971, 'max_cum_ms': 13.4394}
W audit_batch_write_ns: {'n': 29800, 'mean_ms': 1.019, 'p50_ms': 0.9708, 'p90_ms': 1.2042, 'p99_ms': 1.5483, 'p99.9_ms': 7.0451, 'max_cum_ms': 14.074}
W counts: {"admitted": 14988, "audit_enqueued": 29804, "audit_written": 29804, "background_round_trips": 21462, "disposition_ALLOW": 14798, "disposition_BLOCK": 190, "guard_windows": 24674, "lease_refills": 69, "provider_calls": 14798, "provider_connections_opened": 3343, "requests_by_round_trips{n=\"0\"}": 14919, "requests_by_round_trips{n=\"1\"}": 69, "shared_state_round_trips": 69}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 0.074}, "per_proc_util": {"nginx": [0.0, 0.003, 0.003, 0.004, 0.004, 0.004, 0.004, 0.004, 0.004, 0.005, 0.005, 0.005, 0.005, 0.006, 0.006, 0.006, 0.007]}, "per_core_util": {"max": 0.006, "mean": 0.005, "sum": 0.08, "n": 16}, "nic": null, "restarted": [], "loadavg_max": 0.28} nginx cpu-ms/req 1.495
redis: ops/s 696.4 ops/req 13.927 cpu cores 0.005 clients 289 mem 437.4MB ping(us) {'n': 28339, 'p50_us': 485.6, 'p90_us': 517.9, 'p99_us': 619.1, 'p99.9_us': 1332.5, 'max_us': 3033.2, 'mean_us': 495.6}
redis cmdstats: {'info': {'calls_per_s': 0.0, 'usec_per_call': 129.0}, 'hgetall': {'calls_per_s': 358.6, 'usec_per_call': 0.2}, 'evalsha': {'calls_per_s': 0.2, 'usec_per_call': 12.65}, 'xadd': {'calls_per_s': 99.3, 'usec_per_call': 3.4}, 'decrby': {'calls_per_s': 0.2, 'usec_per_call': 0.33}, 'mget': {'calls_per_s': 143.4, 'usec_per_call': 0.34}, 'get': {'calls_per_s': 0.2, 'usec_per_call': 0.61}, 'ping': {'calls_per_s': 94.3, 'usec_per_call': 0.09}}
wire: {'requests_in_window': 15000, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56346.8, 'client_side_to_unit': 9507.1, 'unit_to_provider': 10265.9, 'provider_to_unit': 57042.0, 'unit_to_redis': 5973.2, 'redis_to_unit': 559.6}, 'edge_ip_bytes_per_req': {'edge_to_clients': 56472.6, 'clients_to_edge': 8884.7}, 'olg_resp_body_bytes_mean': {'sse': 67125.6, 'json': 1435.3}}
