# uz4-600: strict FAIL | load-knee PASS (sut, units=4, rate=600)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 180000 (600.0/s) qualified 176891 (589.64/s) FP-blocks 3109 (0.01727) infra 0 (0.0) drops 0 safety 0
infra reasons: {}
infra detail: {}

T_fw_addon: n=176891 p50=47.8426 p90=55.2584 p99=72.5724 p99.9=80.8873 max=106.7052 mean=38.8558
T_fw_addon_nohold: n=176891 p50=11.0582 p90=14.5587 p99=18.9163 p99.9=24.8396 max=45.0831 mean=10.7383
T_fw_addon_sse: n=123857 p50=50.8452 p90=56.5281 p99=73.3651 p99.9=86.9188 max=106.7052 mean=50.8657
T_fw_addon_json: n=53034 p50=11.1205 p90=14.6642 p99=19.01 p99.9=24.8661 max=43.2493 mean=10.8076
T_addon_first_sse: n=123857 p50=10.9847 p90=14.7285 p99=30.1044 p99.9=36.3696 max=56.9573 mean=10.9493
T_addon_total_sse: n=123857 p50=11.033 p90=14.5162 p99=18.8642 p99.9=24.753 max=45.0831 mean=10.7086
T_addon_total_json: n=53034 p50=11.1205 p90=14.6642 p99=19.01 p99.9=24.8661 max=43.2493 mean=10.8076
T_release_lag_max: n=123857 p50=50.8452 p90=56.5281 p99=73.3651 p99.9=86.9188 max=106.7052 mean=50.8657
lateness: n=180000 p50=0.0865 p90=0.0958 p99=0.1077 p99.9=0.1378 max=0.625 mean=0.0864
loadgen: [{'vm': 'rv-pbf-lg-1', 'busy_max': 14.2, 'busy_mean': 13.6, 'late_max_us': 1534, 'conn_opens': 964, 'max_inflight': 959}, {'vm': 'rv-pbf-lg-2', 'busy_max': 14.0, 'busy_mean': 13.4, 'late_max_us': 624, 'conn_opens': 962, 'max_inflight': 958}, {'vm': 'rv-pbf-lg-3', 'busy_max': 14.0, 'busy_mean': 13.5, 'late_max_us': 534, 'conn_opens': 960, 'max_inflight': 957}]  provider_cpu_busy_max: 24.464646224350027
gateway cores total 20.56 cpu-ms/req {'gateway': 34.374, 'workers': 30.408, 'owners': 3.963}
  rv-pbf-unit-2: cores {'launcher': 0.0, 'owner': 0.59, 'owner0': 0.295, 'owner1': 0.295, 'redis': 0.001, 'worker': 4.476} worker util max 0.296 per-core max 0.235 mean 0.214 gpu {'0': {'n': 298, 'sm_mean': 23.2, 'sm_p95': 35.0, 'sm_max': 46.0}, '1': {'n': 298, 'sm_mean': 22.5, 'sm_p95': 33.0, 'sm_max': 41.0}} t_input_p99 14.0902 per-worker admitted {'n': 18, 'min': 1831, 'max': 3074, 'mean': 2497.6, 'max_over_mean': 1.231, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
  rv-pbf-unit-3: cores {'launcher': 0.0, 'owner': 0.598, 'owner0': 0.28, 'owner1': 0.317, 'redis': 0.001, 'worker': 4.705} worker util max 0.321 per-core max 0.242 mean 0.224 gpu {'0': {'n': 298, 'sm_mean': 20.9, 'sm_p95': 31.0, 'sm_max': 42.0}, '1': {'n': 298, 'sm_mean': 23.9, 'sm_p95': 35.0, 'sm_max': 46.0}} t_input_p99 14.3524 per-worker admitted {'n': 18, 'min': 2004, 'max': 3248, 'mean': 2498.0, 'max_over_mean': 1.3, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
  rv-pbf-unit-4: cores {'launcher': 0.0, 'owner': 0.591, 'owner0': 0.292, 'owner1': 0.299, 'redis': 0.001, 'worker': 4.383} worker util max 0.29 per-core max 0.225 mean 0.21 gpu {'0': {'n': 298, 'sm_mean': 22.9, 'sm_p95': 34.0, 'sm_max': 48.0}, '1': {'n': 298, 'sm_mean': 22.5, 'sm_p95': 33.0, 'sm_max': 41.0}} t_input_p99 13.8281 per-worker admitted {'n': 18, 'min': 1833, 'max': 3014, 'mean': 2499.4, 'max_over_mean': 1.206, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
  rv-pbf-unit-5: cores {'launcher': 0.0, 'owner': 0.591, 'owner0': 0.307, 'owner1': 0.283, 'redis': 0.001, 'worker': 4.62} worker util max 0.313 per-core max 0.236 mean 0.22 gpu {'0': {'n': 298, 'sm_mean': 22.9, 'sm_p95': 35.0, 'sm_max': 41.0}, '1': {'n': 298, 'sm_mean': 21.8, 'sm_p95': 33.0, 'sm_max': 39.0}} t_input_p99 13.9592 per-worker admitted {'n': 18, 'min': 1944, 'max': 3153, 'mean': 2496.6, 'max_over_mean': 1.263, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
W t_input_ns: {'n': 179851, 'mean_ms': 8.174, 'p50_ms': 8.7163, 'p90_ms': 11.3377, 'p99_ms': 14.0902, 'p99.9_ms': 19.7919, 'max_cum_ms': 47.9697}
W t_tokenize_ns: {'n': 179848, 'mean_ms': 3.2073, 'p50_ms': 3.2276, 'p90_ms': 5.0135, 'p99_ms': 5.7999, 'p99.9_ms': 6.5208, 'max_cum_ms': 7.9125}
W t_guard_wait_ns: {'n': 179851, 'mean_ms': 4.3788, 'p50_ms': 4.8169, 'p90_ms': 6.7174, 'p99_ms': 8.9784, 'p99.9_ms': 14.2213, 'max_cum_ms': 46.4385}
W guard_owner_rtt_ns: {'n': 179851, 'mean_ms': 4.5509, 'p50_ms': 5.0135, 'p90_ms': 6.783, 'p99_ms': 8.5852, 'p99.9_ms': 13.0417, 'max_cum_ms': 45.1939}
W guard_queue_ns: {'n': 179851, 'mean_ms': 0.2282, 'p50_ms': 0.1009, 'p90_ms': 0.1608, 'p99_ms': 3.1621, 'p99.9_ms': 4.8824, 'max_cum_ms': 11.4164}
W guard_exec_ns: {'n': 179851, 'mean_ms': 3.5402, 'p50_ms': 4.2271, 'p90_ms': 4.4237, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 9.4796}
W t_admit_ns: {'n': 179847, 'mean_ms': 0.0926, 'p50_ms': 0.0865, 'p90_ms': 0.1091, 'p99_ms': 0.1423, 'p99.9_ms': 0.9626, 'max_cum_ms': 10.0495}
W release_processing_ns: {'n': 27722013, 'mean_ms': 0.064, 'p50_ms': 0.0627, 'p90_ms': 0.0845, 'p99_ms': 0.1121, 'p99.9_ms': 0.1423, 'max_cum_ms': 31.4805}
W loop_lag_ns: {'n': 214490, 'mean_ms': 0.8673, 'p50_ms': 0.0148, 'p90_ms': 3.5553, 'p99_ms': 9.3716, 'p99.9_ms': 12.1242, 'max_cum_ms': 34.298}
W audit_batch_write_ns: {'n': 356184, 'mean_ms': 1.0549, 'p50_ms': 0.938, 'p90_ms': 1.237, 'p99_ms': 4.8824, 'p99.9_ms': 10.4202, 'max_cum_ms': 49.1949}
W counts: {"admitted": 179847, "audit_enqueued": 356908, "audit_written": 356908, "background_round_trips": 42898, "disposition_ALLOW": 176744, "disposition_BLOCK": 3107, "guard_windows": 294739, "lease_refills": 826, "provider_calls": 176744, "provider_connections_opened": 40713, "requests_by_round_trips{n=\"0\"}": 179021, "requests_by_round_trips{n=\"1\"}": 826, "shared_state_round_trips": 826}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 1.139}, "per_proc_util": {"nginx": [0.0, 0.057, 0.065, 0.067, 0.067, 0.068, 0.069, 0.071, 0.071, 0.072, 0.072, 0.073, 0.074, 0.077, 0.078, 0.079, 0.08]}, "per_core_util": {"max": 0.073, "mean": 0.072, "sum": 1.15, "n": 16}, "nic": null, "restarted": [], "loadavg_max": 1.45} nginx cpu-ms/req 1.905
redis: ops/s 1791.3 ops/req 2.986 cpu cores 0.033 clients 361 mem 1222.8MB ping(us) {'n': 28420, 'p50_us': 442.6, 'p90_us': 471.6, 'p99_us': 557.5, 'p99.9_us': 2409.3, 'max_us': 6433.7, 'mean_us': 454.7}
redis cmdstats: {'ping': {'calls_per_s': 94.6, 'usec_per_call': 0.12}, 'hgetall': {'calls_per_s': 357.1, 'usec_per_call': 0.26}, 'decrby': {'calls_per_s': 2.8, 'usec_per_call': 0.37}, 'hello': {'calls_per_s': 0.1, 'usec_per_call': 3.21}, 'mget': {'calls_per_s': 142.8, 'usec_per_call': 0.44}, 'xadd': {'calls_per_s': 1188.5, 'usec_per_call': 5.79}, 'info': {'calls_per_s': 0.0, 'usec_per_call': 135.0}, 'get': {'calls_per_s': 2.8, 'usec_per_call': 0.52}, 'evalsha': {'calls_per_s': 2.8, 'usec_per_call': 10.58}}
wire: {'requests_in_window': 180000, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56553.6, 'client_side_to_unit': 8905.9, 'unit_to_provider': 9641.2, 'provider_to_unit': 57249.4, 'unit_to_redis': 5451.3, 'redis_to_unit': 291.5}, 'edge_ip_bytes_per_req': {'edge_to_clients': 56666.2, 'clients_to_edge': 7863.0}, 'olg_resp_body_bytes_mean': {'sse': 67896.9, 'json': 1424.3}}
