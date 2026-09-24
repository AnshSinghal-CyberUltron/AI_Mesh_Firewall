# uz4-600-r3: strict FAIL | load-knee PASS (sut, units=4, rate=600)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 180000 (600.0/s) qualified 176835 (589.45/s) FP-blocks 3160 (0.01756) infra 5 (2.777777777777778e-05) drops 0 safety 0
infra reasons: {'block_on_unavailable_sem': 5}
infra detail: {'403 http_403 type=policy_violation code=blocked_by_policy': 5}

T_fw_addon: n=176835 p50=47.9086 p90=55.4455 p99=72.6157 p99.9=81.6611 max=111.4922 mean=38.9743
T_fw_addon_nohold: n=176835 p50=11.1168 p90=14.6625 p99=19.3414 p99.9=25.2281 max=51.3316 mean=10.811
T_fw_addon_sse: n=123825 p50=50.937 p90=56.7962 p99=73.5518 p99.9=86.9054 max=111.4922 mean=51.0054
T_fw_addon_json: n=53010 p50=11.1751 p90=14.7618 p99=19.6342 p99.9=24.9874 max=48.5017 mean=10.8712
T_addon_first_sse: n=123825 p50=11.0362 p90=14.8547 p99=30.1047 p99.9=36.6422 max=68.1668 mean=11.0157
T_addon_total_sse: n=123825 p50=11.0912 p90=14.6202 p99=19.2098 p99.9=25.419 max=51.3316 mean=10.7852
T_addon_total_json: n=53010 p50=11.1751 p90=14.7618 p99=19.6342 p99.9=24.9874 max=48.5017 mean=10.8712
T_release_lag_max: n=123825 p50=50.937 p90=56.7962 p99=73.5518 p99.9=86.9054 max=111.4922 mean=51.0054
lateness: n=180000 p50=0.0867 p90=0.096 p99=0.1071 p99.9=0.1332 max=0.7266 mean=0.0861
loadgen: [{'vm': 'rv-pbf-lg-1', 'busy_max': 14.4, 'busy_mean': 13.7, 'late_max_us': 1359, 'conn_opens': 961, 'max_inflight': 958}, {'vm': 'rv-pbf-lg-2', 'busy_max': 15.9, 'busy_mean': 13.4, 'late_max_us': 629, 'conn_opens': 963, 'max_inflight': 959}, {'vm': 'rv-pbf-lg-3', 'busy_max': 14.1, 'busy_mean': 13.5, 'late_max_us': 676, 'conn_opens': 961, 'max_inflight': 958}]  provider_cpu_busy_max: 17.879125869917843
gateway cores total 20.65 cpu-ms/req {'gateway': 34.53, 'workers': 30.56, 'owners': 3.967}
  rv-pbf-unit-2: cores {'launcher': 0.0, 'owner': 0.591, 'owner0': 0.305, 'owner1': 0.285, 'redis': 0.001, 'worker': 4.484} worker util max 0.306 per-core max 0.233 mean 0.215 gpu {'0': {'n': 298, 'sm_mean': 23.2, 'sm_p95': 34.0, 'sm_max': 39.0}, '1': {'n': 298, 'sm_mean': 21.9, 'sm_p95': 31.0, 'sm_max': 39.0}} t_input_p99 14.2213 per-worker admitted {'n': 18, 'min': 1779, 'max': 3275, 'mean': 2497.3, 'max_over_mean': 1.311, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
  rv-pbf-unit-3: cores {'launcher': 0.0, 'owner': 0.598, 'owner0': 0.293, 'owner1': 0.305, 'redis': 0.001, 'worker': 4.712} worker util max 0.306 per-core max 0.239 mean 0.224 gpu {'0': {'n': 298, 'sm_mean': 22.0, 'sm_p95': 32.0, 'sm_max': 40.0}, '1': {'n': 298, 'sm_mean': 23.1, 'sm_p95': 35.0, 'sm_max': 41.0}} t_input_p99 14.4835 per-worker admitted {'n': 18, 'min': 1456, 'max': 3023, 'mean': 2495.7, 'max_over_mean': 1.211, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
  rv-pbf-unit-4: cores {'launcher': 0.0, 'owner': 0.594, 'owner0': 0.309, 'owner1': 0.284, 'redis': 0.001, 'worker': 4.437} worker util max 0.283 per-core max 0.226 mean 0.213 gpu {'0': {'n': 298, 'sm_mean': 23.7, 'sm_p95': 35.0, 'sm_max': 41.0}, '1': {'n': 298, 'sm_mean': 21.6, 'sm_p95': 33.0, 'sm_max': 52.0}} t_input_p99 14.0902 per-worker admitted {'n': 18, 'min': 1849, 'max': 2985, 'mean': 2497.7, 'max_over_mean': 1.195, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
  rv-pbf-unit-5: cores {'launcher': 0.0, 'owner': 0.59, 'owner0': 0.299, 'owner1': 0.292, 'redis': 0.001, 'worker': 4.641} worker util max 0.305 per-core max 0.235 mean 0.221 gpu {'0': {'n': 298, 'sm_mean': 22.6, 'sm_p95': 34.0, 'sm_max': 37.0}, '1': {'n': 298, 'sm_mean': 22.2, 'sm_p95': 35.0, 'sm_max': 43.0}} t_input_p99 14.3524 per-worker admitted {'n': 18, 'min': 1828, 'max': 3074, 'mean': 2494.4, 'max_over_mean': 1.232, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
W t_input_ns: {'n': 179738, 'mean_ms': 8.2256, 'p50_ms': 8.8474, 'p90_ms': 11.4688, 'p99_ms': 14.3524, 'p99.9_ms': 20.054, 'max_cum_ms': 49.401}
W t_tokenize_ns: {'n': 179732, 'mean_ms': 3.2193, 'p50_ms': 3.2276, 'p90_ms': 5.0135, 'p99_ms': 5.8655, 'p99.9_ms': 6.5864, 'max_cum_ms': 8.4915}
W t_guard_wait_ns: {'n': 179738, 'mean_ms': 4.4113, 'p50_ms': 4.8169, 'p90_ms': 6.783, 'p99_ms': 9.3716, 'p99.9_ms': 14.4835, 'max_cum_ms': 46.4385}
W guard_owner_rtt_ns: {'n': 179738, 'mean_ms': 4.5834, 'p50_ms': 5.0135, 'p90_ms': 6.9796, 'p99_ms': 8.9784, 'p99.9_ms': 13.3038, 'max_cum_ms': 45.2496}
W guard_queue_ns: {'n': 179733, 'mean_ms': 0.2412, 'p50_ms': 0.1019, 'p90_ms': 0.1669, 'p99_ms': 3.2932, 'p99.9_ms': 5.2756, 'max_cum_ms': 11.4231}
W guard_exec_ns: {'n': 179733, 'mean_ms': 3.5414, 'p50_ms': 4.2271, 'p90_ms': 4.4892, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 9.4796}
W t_admit_ns: {'n': 179732, 'mean_ms': 0.0936, 'p50_ms': 0.0876, 'p90_ms': 0.1101, 'p99_ms': 0.1444, 'p99.9_ms': 0.9871, 'max_cum_ms': 32.41}
W release_processing_ns: {'n': 27681855, 'mean_ms': 0.0642, 'p50_ms': 0.0632, 'p90_ms': 0.0855, 'p99_ms': 0.1132, 'p99.9_ms': 0.1444, 'max_cum_ms': 34.5668}
W loop_lag_ns: {'n': 214120, 'mean_ms': 0.9073, 'p50_ms': 0.0145, 'p90_ms': 3.7519, 'p99_ms': 9.7649, 'p99.9_ms': 12.3863, 'max_cum_ms': 41.6829}
W audit_batch_write_ns: {'n': 355816, 'mean_ms': 1.0602, 'p50_ms': 0.9462, 'p90_ms': 1.2534, 'p99_ms': 4.7514, 'p99.9_ms': 10.5513, 'max_cum_ms': 49.1949}
W counts: {"admitted": 179732, "audit_enqueued": 356541, "audit_written": 356543, "background_round_trips": 42824, "disposition_ALLOW": 176576, "disposition_BLOCK": 3162, "guard_deadline_expired": 5, "guard_unavailable_findings": 5, "guard_windows": 294548, "lease_refills": 828, "provider_calls": 176576, "provider_connections_opened": 40443, "requests_by_round_trips{n=\"0\"}": 178904, "requests_by_round_trips{n=\"1\"}": 828, "shared_state_round_trips": 828}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 1.139}, "per_proc_util": {"nginx": [0.0, 0.062, 0.064, 0.065, 0.067, 0.07, 0.071, 0.071, 0.072, 0.072, 0.073, 0.074, 0.074, 0.074, 0.075, 0.076, 0.08]}, "per_core_util": {"max": 0.073, "mean": 0.072, "sum": 1.15, "n": 16}, "nic": null, "restarted": [], "loadavg_max": 1.22} nginx cpu-ms/req 1.905
redis: ops/s 1790.2 ops/req 2.984 cpu cores 0.033 clients 361 mem 5086.7MB ping(us) {'n': 28260, 'p50_us': 501.0, 'p90_us': 529.1, 'p99_us': 608.9, 'p99.9_us': 2624.9, 'max_us': 5691.9, 'mean_us': 513.2}
redis cmdstats: {'ping': {'calls_per_s': 94.0, 'usec_per_call': 0.12}, 'hgetall': {'calls_per_s': 356.9, 'usec_per_call': 0.27}, 'decrby': {'calls_per_s': 2.8, 'usec_per_call': 0.38}, 'mget': {'calls_per_s': 142.8, 'usec_per_call': 0.45}, 'xadd': {'calls_per_s': 1188.2, 'usec_per_call': 5.46}, 'info': {'calls_per_s': 0.0, 'usec_per_call': 115.0}, 'get': {'calls_per_s': 2.8, 'usec_per_call': 0.53}, 'evalsha': {'calls_per_s': 2.8, 'usec_per_call': 10.36}}
wire: {'requests_in_window': 180000, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56542.1, 'client_side_to_unit': 8908.2, 'unit_to_provider': 9655.3, 'provider_to_unit': 57237.0, 'unit_to_redis': 5450.1, 'redis_to_unit': 291.3}, 'edge_ip_bytes_per_req': {'edge_to_clients': 56656.1, 'clients_to_edge': 7862.8}, 'olg_resp_body_bytes_mean': {'sse': 67902.0, 'json': 1424.4}}
