# uz4-600-r2: strict FAIL | load-knee PASS (sut, units=4, rate=600)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 180000 (600.0/s) qualified 176900 (589.67/s) FP-blocks 3099 (0.01722) infra 1 (5.555555555555556e-06) drops 0 safety 0
infra reasons: {'block_on_unavailable_sem': 1}
infra detail: {'403 http_403 type=policy_violation code=blocked_by_policy': 1}

T_fw_addon: n=176900 p50=47.8761 p90=55.3944 p99=72.6013 p99.9=81.1182 max=112.8406 mean=38.9622
T_fw_addon_nohold: n=176900 p50=11.1147 p90=14.6311 p99=19.1595 p99.9=24.4314 max=49.9187 mean=10.7879
T_fw_addon_sse: n=123864 p50=50.9223 p90=56.7062 p99=73.4013 p99.9=86.9578 max=112.8406 mean=51.0024
T_fw_addon_json: n=53036 p50=11.1732 p90=14.7023 p99=19.0721 p99.9=24.224 max=42.6327 mean=10.8427
T_addon_first_sse: n=123864 p50=11.038 p90=14.8029 p99=29.8862 p99.9=35.8116 max=58.3281 mean=10.9837
T_addon_total_sse: n=123864 p50=11.0904 p90=14.5997 p99=19.1872 p99.9=24.6412 max=49.9187 mean=10.7645
T_addon_total_json: n=53036 p50=11.1732 p90=14.7023 p99=19.0721 p99.9=24.224 max=42.6327 mean=10.8427
T_release_lag_max: n=123864 p50=50.9223 p90=56.7062 p99=73.4013 p99.9=86.9578 max=112.8406 mean=51.0024
lateness: n=180000 p50=0.0866 p90=0.0958 p99=0.1077 p99.9=0.1287 max=0.6262 mean=0.0868
loadgen: [{'vm': 'rv-pbf-lg-1', 'busy_max': 14.2, 'busy_mean': 13.7, 'late_max_us': 1563, 'conn_opens': 962, 'max_inflight': 958}, {'vm': 'rv-pbf-lg-2', 'busy_max': 14.1, 'busy_mean': 13.4, 'late_max_us': 356, 'conn_opens': 960, 'max_inflight': 957}, {'vm': 'rv-pbf-lg-3', 'busy_max': 14.0, 'busy_mean': 13.5, 'late_max_us': 586, 'conn_opens': 963, 'max_inflight': 959}]  provider_cpu_busy_max: 17.838511856928495
gateway cores total 20.7 cpu-ms/req {'gateway': 34.615, 'workers': 30.632, 'owners': 3.979}
  rv-pbf-unit-2: cores {'launcher': 0.0, 'owner': 0.593, 'owner0': 0.283, 'owner1': 0.31, 'redis': 0.001, 'worker': 4.494} worker util max 0.286 per-core max 0.231 mean 0.215 gpu {'0': {'n': 298, 'sm_mean': 22.1, 'sm_p95': 35.0, 'sm_max': 50.0}, '1': {'n': 298, 'sm_mean': 23.1, 'sm_p95': 33.0, 'sm_max': 44.0}} t_input_p99 14.0902 per-worker admitted {'n': 18, 'min': 1946, 'max': 2927, 'mean': 2497.7, 'max_over_mean': 1.172, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
  rv-pbf-unit-3: cores {'launcher': 0.0, 'owner': 0.599, 'owner0': 0.29, 'owner1': 0.309, 'redis': 0.001, 'worker': 4.728} worker util max 0.3 per-core max 0.243 mean 0.225 gpu {'0': {'n': 298, 'sm_mean': 22.3, 'sm_p95': 33.0, 'sm_max': 39.0}, '1': {'n': 298, 'sm_mean': 23.1, 'sm_p95': 33.0, 'sm_max': 44.0}} t_input_p99 14.3524 per-worker admitted {'n': 18, 'min': 2122, 'max': 2900, 'mean': 2501.4, 'max_over_mean': 1.159, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
  rv-pbf-unit-4: cores {'launcher': 0.0, 'owner': 0.595, 'owner0': 0.299, 'owner1': 0.296, 'redis': 0.001, 'worker': 4.448} worker util max 0.297 per-core max 0.228 mean 0.213 gpu {'0': {'n': 298, 'sm_mean': 23.4, 'sm_p95': 34.0, 'sm_max': 41.0}, '1': {'n': 298, 'sm_mean': 22.1, 'sm_p95': 34.0, 'sm_max': 39.0}} t_input_p99 13.8281 per-worker admitted {'n': 18, 'min': 1979, 'max': 3115, 'mean': 2497.4, 'max_over_mean': 1.247, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
  rv-pbf-unit-5: cores {'launcher': 0.0, 'owner': 0.593, 'owner0': 0.293, 'owner1': 0.3, 'redis': 0.001, 'worker': 4.647} worker util max 0.308 per-core max 0.237 mean 0.221 gpu {'0': {'n': 298, 'sm_mean': 22.4, 'sm_p95': 33.0, 'sm_max': 47.0}, '1': {'n': 298, 'sm_mean': 23.2, 'sm_p95': 35.0, 'sm_max': 42.0}} t_input_p99 14.0902 per-worker admitted {'n': 18, 'min': 1958, 'max': 3126, 'mean': 2504.1, 'max_over_mean': 1.248, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
W t_input_ns: {'n': 180012, 'mean_ms': 8.2089, 'p50_ms': 8.8474, 'p90_ms': 11.3377, 'p99_ms': 14.0902, 'p99.9_ms': 19.5297, 'max_cum_ms': 49.401}
W t_tokenize_ns: {'n': 180009, 'mean_ms': 3.2318, 'p50_ms': 3.2604, 'p90_ms': 5.079, 'p99_ms': 5.7999, 'p99.9_ms': 6.5208, 'max_cum_ms': 8.4915}
W t_guard_wait_ns: {'n': 180012, 'mean_ms': 4.3859, 'p50_ms': 4.8169, 'p90_ms': 6.7174, 'p99_ms': 8.8474, 'p99.9_ms': 14.3524, 'max_cum_ms': 46.4385}
W guard_owner_rtt_ns: {'n': 180011, 'mean_ms': 4.559, 'p50_ms': 5.0135, 'p90_ms': 6.8485, 'p99_ms': 8.5852, 'p99.9_ms': 13.1727, 'max_cum_ms': 45.2496}
W guard_queue_ns: {'n': 180010, 'mean_ms': 0.2292, 'p50_ms': 0.1019, 'p90_ms': 0.1649, 'p99_ms': 3.1621, 'p99.9_ms': 4.8169, 'max_cum_ms': 11.4231}
W guard_exec_ns: {'n': 180010, 'mean_ms': 3.5436, 'p50_ms': 4.2271, 'p90_ms': 4.4892, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 9.4796}
W t_admit_ns: {'n': 180009, 'mean_ms': 0.0927, 'p50_ms': 0.0865, 'p90_ms': 0.1091, 'p99_ms': 0.1423, 'p99.9_ms': 0.9708, 'max_cum_ms': 32.41}
W release_processing_ns: {'n': 27696830, 'mean_ms': 0.0642, 'p50_ms': 0.0632, 'p90_ms': 0.0855, 'p99_ms': 0.1132, 'p99.9_ms': 0.1444, 'max_cum_ms': 34.5668}
W loop_lag_ns: {'n': 214190, 'mean_ms': 0.901, 'p50_ms': 0.0148, 'p90_ms': 3.7519, 'p99_ms': 9.6338, 'p99.9_ms': 12.1242, 'max_cum_ms': 41.6829}
W audit_batch_write_ns: {'n': 356186, 'mean_ms': 1.0574, 'p50_ms': 0.9462, 'p90_ms': 1.2534, 'p99_ms': 4.8169, 'p99.9_ms': 10.8134, 'max_cum_ms': 49.1949}
W counts: {"admitted": 180009, "audit_enqueued": 356896, "audit_written": 356897, "background_round_trips": 42838, "disposition_ALLOW": 176912, "disposition_BLOCK": 3100, "guard_deadline_expired": 1, "guard_unavailable_findings": 1, "guard_windows": 294999, "lease_refills": 825, "provider_calls": 176912, "provider_connections_opened": 40973, "requests_by_round_trips{n=\"0\"}": 179184, "requests_by_round_trips{n=\"1\"}": 825, "shared_state_round_trips": 825}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 1.14}, "per_proc_util": {"nginx": [0.0, 0.064, 0.066, 0.068, 0.068, 0.069, 0.069, 0.07, 0.07, 0.07, 0.071, 0.071, 0.073, 0.073, 0.075, 0.08, 0.083]}, "per_core_util": {"max": 0.073, "mean": 0.072, "sum": 1.15, "n": 16}, "nic": null, "restarted": [], "loadavg_max": 1.32} nginx cpu-ms/req 1.906
redis: ops/s 1790.3 ops/req 2.984 cpu cores 0.033 clients 361 mem 3866.5MB ping(us) {'n': 28239, 'p50_us': 509.8, 'p90_us': 538.1, 'p99_us': 614.9, 'p99.9_us': 2418.9, 'max_us': 4706.6, 'mean_us': 520.9}
redis cmdstats: {'ping': {'calls_per_s': 93.9, 'usec_per_call': 0.12}, 'hgetall': {'calls_per_s': 356.9, 'usec_per_call': 0.27}, 'decrby': {'calls_per_s': 2.7, 'usec_per_call': 0.38}, 'mget': {'calls_per_s': 142.8, 'usec_per_call': 0.45}, 'xadd': {'calls_per_s': 1188.4, 'usec_per_call': 5.56}, 'info': {'calls_per_s': 0.0, 'usec_per_call': 122.0}, 'get': {'calls_per_s': 2.7, 'usec_per_call': 0.57}, 'evalsha': {'calls_per_s': 2.7, 'usec_per_call': 10.64}}
wire: {'requests_in_window': 180000, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56564.6, 'client_side_to_unit': 8927.0, 'unit_to_provider': 9664.3, 'provider_to_unit': 57261.6, 'unit_to_redis': 5458.9, 'redis_to_unit': 291.7}, 'edge_ip_bytes_per_req': {'edge_to_clients': 56667.3, 'clients_to_edge': 7857.1}, 'olg_resp_body_bytes_mean': {'sse': 67895.0, 'json': 1424.1}}
