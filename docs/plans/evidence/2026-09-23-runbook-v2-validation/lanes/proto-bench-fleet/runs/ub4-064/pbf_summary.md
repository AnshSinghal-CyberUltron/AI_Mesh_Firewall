# ub4-064: strict FAIL | load-knee PASS (sut, units=4, rate=64)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 19200 (64.0/s) qualified 18909 (63.03/s) FP-blocks 291 (0.01516) infra 0 (0.0) drops 0 safety 0
infra reasons: {}
infra detail: {}

T_fw_addon: n=18909 p50=46.9356 p90=53.0422 p99=71.2168 p99.9=75.3944 max=97.5873 mean=37.41
T_fw_addon_nohold: n=18909 p50=10.2262 p90=12.8004 p99=15.3715 p99.9=18.5967 max=21.6033 mean=9.6495
T_fw_addon_sse: n=13230 p50=49.953 p90=53.8576 p99=71.6144 p99.9=76.7336 max=97.5873 mean=49.3052
T_fw_addon_json: n=5679 p50=10.2966 p90=12.8346 p99=15.4304 p99.9=18.7565 max=21.4086 mean=9.6987
T_addon_first_sse: n=13230 p50=10.1405 p90=13.308 p99=29.9721 p99.9=34.1222 max=50.4184 mean=9.8912
T_addon_total_sse: n=13230 p50=10.1974 p90=12.7833 p99=15.3678 p99.9=18.299 max=21.6033 mean=9.6283
T_addon_total_json: n=5679 p50=10.2966 p90=12.8346 p99=15.4304 p99.9=18.7565 max=21.4086 mean=9.6987
T_release_lag_max: n=13230 p50=49.953 p90=53.8576 p99=71.6144 p99.9=76.7336 max=97.5873 mean=49.3052
lateness: n=19200 p50=0.0866 p90=0.0964 p99=0.1091 p99.9=0.1209 max=0.2301 mean=0.0866
loadgen: [{'vm': 'rv-pbf-lg-1', 'busy_max': 4.2, 'busy_mean': 3.9, 'late_max_us': 260, 'conn_opens': 127, 'max_inflight': 117}, {'vm': 'rv-pbf-lg-2', 'busy_max': 4.2, 'busy_mean': 4.0, 'late_max_us': 230, 'conn_opens': 128, 'max_inflight': 117}, {'vm': 'rv-pbf-lg-3', 'busy_max': 4.2, 'busy_mean': 3.9, 'late_max_us': 259, 'conn_opens': 128, 'max_inflight': 117}]  provider_cpu_busy_max: 18.27507997533836
gateway cores total 2.85 cpu-ms/req {'gateway': 44.607, 'workers': 40.24, 'owners': 4.341}
  rv-pbf-unit-2: cores {'launcher': 0.0, 'owner': 0.068, 'owner0': 0.035, 'owner1': 0.033, 'redis': 0.001, 'worker': 0.61} worker util max 0.044 per-core max 0.04 mean 0.031 gpu {'0': {'n': 298, 'sm_mean': 2.5, 'sm_p95': 7.0, 'sm_max': 11.0}, '1': {'n': 298, 'sm_mean': 2.4, 'sm_p95': 8.0, 'sm_max': 14.0}} t_input_p99 12.1242 per-worker admitted {'n': 18, 'min': 150, 'max': 405, 'mean': 266.4, 'max_over_mean': 1.52, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
  rv-pbf-unit-3: cores {'launcher': 0.0, 'owner': 0.068, 'owner0': 0.036, 'owner1': 0.032, 'redis': 0.001, 'worker': 0.601} worker util max 0.042 per-core max 0.037 mean 0.03 gpu {'0': {'n': 298, 'sm_mean': 2.6, 'sm_p95': 7.0, 'sm_max': 11.0}, '1': {'n': 298, 'sm_mean': 2.5, 'sm_p95': 7.0, 'sm_max': 11.0}} t_input_p99 12.2552 per-worker admitted {'n': 18, 'min': 145, 'max': 385, 'mean': 266.1, 'max_over_mean': 1.447, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
  rv-pbf-unit-4: cores {'launcher': 0.0, 'owner': 0.072, 'owner0': 0.034, 'owner1': 0.038, 'redis': 0.001, 'worker': 0.741} worker util max 0.06 per-core max 0.047 mean 0.036 gpu {'0': {'n': 298, 'sm_mean': 2.4, 'sm_p95': 8.0, 'sm_max': 11.0}, '1': {'n': 298, 'sm_mean': 2.5, 'sm_p95': 7.0, 'sm_max': 11.0}} t_input_p99 12.7795 per-worker admitted {'n': 18, 'min': 145, 'max': 477, 'mean': 266.7, 'max_over_mean': 1.789, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
  rv-pbf-unit-5: cores {'launcher': 0.0, 'owner': 0.069, 'owner0': 0.032, 'owner1': 0.037, 'redis': 0.001, 'worker': 0.614} worker util max 0.058 per-core max 0.041 mean 0.03 gpu {'0': {'n': 298, 'sm_mean': 2.2, 'sm_p95': 7.0, 'sm_max': 12.0}, '1': {'n': 298, 'sm_mean': 2.7, 'sm_p95': 8.0, 'sm_max': 12.0}} t_input_p99 12.3863 per-worker admitted {'n': 18, 'min': 14, 'max': 517, 'mean': 266.5, 'max_over_mean': 1.94, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
W t_input_ns: {'n': 19183, 'mean_ms': 7.5229, 'p50_ms': 8.2248, 'p90_ms': 10.027, 'p99_ms': 12.5174, 'p99.9_ms': 15.2699, 'max_cum_ms': 29.3969}
W t_tokenize_ns: {'n': 19183, 'mean_ms': 2.6376, 'p50_ms': 2.6706, 'p90_ms': 4.0141, 'p99_ms': 4.5548, 'p99.9_ms': 5.4723, 'max_cum_ms': 7.6241}
W t_guard_wait_ns: {'n': 19183, 'mean_ms': 4.3365, 'p50_ms': 4.8824, 'p90_ms': 5.4067, 'p99_ms': 7.5694, 'p99.9_ms': 11.0756, 'max_cum_ms': 15.6537}
W guard_owner_rtt_ns: {'n': 19183, 'mean_ms': 4.516, 'p50_ms': 5.079, 'p90_ms': 5.6033, 'p99_ms': 7.766, 'p99.9_ms': 10.4202, 'max_cum_ms': 17.0599}
W guard_queue_ns: {'n': 19183, 'mean_ms': 0.1157, 'p50_ms': 0.1142, 'p90_ms': 0.1362, 'p99_ms': 0.1628, 'p99.9_ms': 0.7905, 'max_cum_ms': 1.5889}
W guard_exec_ns: {'n': 19183, 'mean_ms': 3.6922, 'p50_ms': 4.2926, 'p90_ms': 4.6858, 'p99_ms': 6.7174, 'p99.9_ms': 6.914, 'max_cum_ms': 9.296}
W t_admit_ns: {'n': 19183, 'mean_ms': 0.0979, 'p50_ms': 0.0916, 'p90_ms': 0.108, 'p99_ms': 0.1341, 'p99.9_ms': 1.0445, 'max_cum_ms': 7.6442}
W release_processing_ns: {'n': 2929884, 'mean_ms': 0.0712, 'p50_ms': 0.0681, 'p90_ms': 0.0947, 'p99_ms': 0.1203, 'p99.9_ms': 0.1485, 'max_cum_ms': 1.4361}
W loop_lag_ns: {'n': 214690, 'mean_ms': 0.6494, 'p50_ms': 0.017, 'p90_ms': 1.0895, 'p99_ms': 6.2587, 'p99.9_ms': 7.7005, 'max_cum_ms': 13.4394}
W audit_batch_write_ns: {'n': 38110, 'mean_ms': 0.9842, 'p50_ms': 0.938, 'p90_ms': 1.1715, 'p99_ms': 1.3681, 'p99.9_ms': 6.9796, 'max_cum_ms': 14.074}
W counts: {"admitted": 19183, "audit_enqueued": 38114, "audit_written": 38114, "background_round_trips": 42938, "disposition_ALLOW": 18892, "disposition_BLOCK": 291, "guard_windows": 31652, "lease_refills": 89, "provider_calls": 18892, "provider_connections_opened": 2175, "requests_by_round_trips{n=\"0\"}": 19094, "requests_by_round_trips{n=\"1\"}": 89, "shared_state_round_trips": 89}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 0.076}, "per_proc_util": {"nginx": [0.0, 0.003, 0.003, 0.004, 0.004, 0.004, 0.004, 0.005, 0.005, 0.005, 0.005, 0.005, 0.005, 0.005, 0.006, 0.006, 0.006]}, "per_core_util": {"max": 0.006, "mean": 0.005, "sum": 0.08, "n": 16}, "nic": null, "restarted": [], "loadavg_max": 0.17} nginx cpu-ms/req 1.195
redis: ops/s 722.7 ops/req 11.293 cpu cores 0.005 clients 361 mem 772.7MB ping(us) {'n': 28262, 'p50_us': 502.4, 'p90_us': 577.8, 'p99_us': 666.7, 'p99.9_us': 1281.5, 'max_us': 4426.2, 'mean_us': 523.5}
redis cmdstats: {'info': {'calls_per_s': 0.0, 'usec_per_call': 122.0}, 'hgetall': {'calls_per_s': 357.7, 'usec_per_call': 0.21}, 'evalsha': {'calls_per_s': 0.3, 'usec_per_call': 11.57}, 'xadd': {'calls_per_s': 127.0, 'usec_per_call': 3.53}, 'decrby': {'calls_per_s': 0.3, 'usec_per_call': 0.47}, 'mget': {'calls_per_s': 143.1, 'usec_per_call': 0.35}, 'get': {'calls_per_s': 0.3, 'usec_per_call': 0.6}, 'ping': {'calls_per_s': 94.0, 'usec_per_call': 0.09}}
wire: {'requests_in_window': 19200, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56099.5, 'client_side_to_unit': 9993.6, 'unit_to_provider': 10434.5, 'provider_to_unit': 56783.7, 'unit_to_redis': 6200.9, 'redis_to_unit': 646.5}, 'edge_ip_bytes_per_req': {'edge_to_clients': 56223.4, 'clients_to_edge': 8513.4}, 'olg_resp_body_bytes_mean': {'sse': 67278.4, 'json': 1432.0}}
