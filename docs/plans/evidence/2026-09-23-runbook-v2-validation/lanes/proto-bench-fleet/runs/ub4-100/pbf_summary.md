# ub4-100: strict FAIL | load-knee PASS (sut, units=4, rate=100)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 30000 (100.0/s) qualified 29466 (98.22/s) FP-blocks 534 (0.0178) infra 0 (0.0) drops 0 safety 0
infra reasons: {}
infra detail: {}

T_fw_addon: n=29466 p50=46.9748 p90=53.1994 p99=71.1799 p99.9=79.4513 max=95.068 mean=37.474
T_fw_addon_nohold: n=29466 p50=10.1782 p90=13.0235 p99=15.6294 p99.9=18.8732 max=22.0671 mean=9.6912
T_fw_addon_sse: n=20615 p50=49.9034 p90=54.0364 p99=71.7302 p99.9=86.542 max=95.068 mean=49.3645
T_fw_addon_json: n=8851 p50=10.265 p90=13.2384 p99=15.7483 p99.9=19.1415 max=22.0671 mean=9.7795
T_addon_first_sse: n=20615 p50=10.084 p90=13.2947 p99=27.4723 p99.9=33.4865 max=52.4369 mean=9.8683
T_addon_total_sse: n=20615 p50=10.1472 p90=12.9125 p99=15.5443 p99.9=18.8732 max=20.6114 mean=9.6533
T_addon_total_json: n=8851 p50=10.265 p90=13.2384 p99=15.7483 p99.9=19.1415 max=22.0671 mean=9.7795
T_release_lag_max: n=20615 p50=49.9034 p90=54.0364 p99=71.7302 p99.9=86.542 max=95.068 mean=49.3645
lateness: n=30000 p50=0.0873 p90=0.1017 p99=0.113 p99.9=0.1297 max=0.2505 mean=0.0883
loadgen: [{'vm': 'rv-pbf-lg-1', 'busy_max': 4.8, 'busy_mean': 4.4, 'late_max_us': 415, 'conn_opens': 197, 'max_inflight': 182}, {'vm': 'rv-pbf-lg-2', 'busy_max': 4.8, 'busy_mean': 4.4, 'late_max_us': 254, 'conn_opens': 197, 'max_inflight': 182}, {'vm': 'rv-pbf-lg-3', 'busy_max': 4.8, 'busy_mean': 4.4, 'late_max_us': 173, 'conn_opens': 197, 'max_inflight': 182}]  provider_cpu_busy_max: 15.738824755343817
gateway cores total 4.02 cpu-ms/req {'gateway': 40.309, 'workers': 36.054, 'owners': 4.238}
  rv-pbf-unit-2: cores {'launcher': 0.0, 'owner': 0.104, 'owner0': 0.054, 'owner1': 0.05, 'redis': 0.001, 'worker': 0.864} worker util max 0.084 per-core max 0.057 mean 0.043 gpu {'0': {'n': 299, 'sm_mean': 4.2, 'sm_p95': 9.0, 'sm_max': 18.0}, '1': {'n': 299, 'sm_mean': 3.5, 'sm_p95': 10.0, 'sm_max': 13.0}} t_input_p99 12.2552 per-worker admitted {'n': 18, 'min': 257, 'max': 848, 'mean': 416.3, 'max_over_mean': 2.037, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
  rv-pbf-unit-3: cores {'launcher': 0.0, 'owner': 0.104, 'owner0': 0.049, 'owner1': 0.055, 'redis': 0.001, 'worker': 0.854} worker util max 0.088 per-core max 0.059 mean 0.042 gpu {'0': {'n': 299, 'sm_mean': 3.7, 'sm_p95': 9.0, 'sm_max': 17.0}, '1': {'n': 299, 'sm_mean': 4.0, 'sm_p95': 10.0, 'sm_max': 15.0}} t_input_p99 12.3863 per-worker admitted {'n': 18, 'min': 153, 'max': 874, 'mean': 416.4, 'max_over_mean': 2.099, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
  rv-pbf-unit-4: cores {'launcher': 0.0, 'owner': 0.11, 'owner0': 0.053, 'owner1': 0.057, 'redis': 0.001, 'worker': 1.018} worker util max 0.082 per-core max 0.058 mean 0.049 gpu {'0': {'n': 298, 'sm_mean': 3.8, 'sm_p95': 10.0, 'sm_max': 15.0}, '1': {'n': 298, 'sm_mean': 4.3, 'sm_p95': 11.0, 'sm_max': 24.0}} t_input_p99 12.9106 per-worker admitted {'n': 18, 'min': 182, 'max': 695, 'mean': 416.2, 'max_over_mean': 1.67, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
  rv-pbf-unit-5: cores {'launcher': 0.0, 'owner': 0.105, 'owner0': 0.059, 'owner1': 0.045, 'redis': 0.001, 'worker': 0.857} worker util max 0.062 per-core max 0.05 mean 0.042 gpu {'0': {'n': 299, 'sm_mean': 4.6, 'sm_p95': 11.0, 'sm_max': 17.0}, '1': {'n': 299, 'sm_mean': 3.3, 'sm_p95': 9.0, 'sm_max': 12.0}} t_input_p99 12.2552 per-worker admitted {'n': 18, 'min': 199, 'max': 571, 'mean': 416.1, 'max_over_mean': 1.372, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
W t_input_ns: {'n': 29970, 'mean_ms': 7.4933, 'p50_ms': 8.1592, 'p90_ms': 10.027, 'p99_ms': 12.6484, 'p99.9_ms': 15.401, 'max_cum_ms': 29.3969}
W t_tokenize_ns: {'n': 29970, 'mean_ms': 2.6369, 'p50_ms': 2.6706, 'p90_ms': 4.0141, 'p99_ms': 4.4892, 'p99.9_ms': 5.8655, 'max_cum_ms': 7.4873}
W t_guard_wait_ns: {'n': 29970, 'mean_ms': 4.3067, 'p50_ms': 4.8169, 'p90_ms': 5.4067, 'p99_ms': 7.5694, 'p99.9_ms': 11.0756, 'max_cum_ms': 15.6537}
W guard_owner_rtt_ns: {'n': 29970, 'mean_ms': 4.4785, 'p50_ms': 5.0135, 'p90_ms': 5.6689, 'p99_ms': 7.766, 'p99.9_ms': 10.5513, 'max_cum_ms': 17.0599}
W guard_queue_ns: {'n': 29970, 'mean_ms': 0.112, 'p50_ms': 0.1111, 'p90_ms': 0.1306, 'p99_ms': 0.1546, 'p99.9_ms': 0.8151, 'max_cum_ms': 1.5889}
W guard_exec_ns: {'n': 29970, 'mean_ms': 3.6658, 'p50_ms': 4.2926, 'p90_ms': 4.6203, 'p99_ms': 6.7174, 'p99.9_ms': 6.914, 'max_cum_ms': 9.296}
W t_admit_ns: {'n': 29970, 'mean_ms': 0.0984, 'p50_ms': 0.0906, 'p90_ms': 0.1142, 'p99_ms': 0.1444, 'p99.9_ms': 1.0445, 'max_cum_ms': 7.6442}
W release_processing_ns: {'n': 4585851, 'mean_ms': 0.0679, 'p50_ms': 0.066, 'p90_ms': 0.0896, 'p99_ms': 0.1172, 'p99.9_ms': 0.1464, 'max_cum_ms': 1.4361}
W loop_lag_ns: {'n': 214630, 'mean_ms': 0.65, 'p50_ms': 0.0141, 'p90_ms': 1.1551, 'p99_ms': 6.3242, 'p99.9_ms': 7.8971, 'max_cum_ms': 13.4394}
W audit_batch_write_ns: {'n': 59379, 'mean_ms': 0.9772, 'p50_ms': 0.9298, 'p90_ms': 1.1387, 'p99_ms': 1.45, 'p99.9_ms': 7.1107, 'max_cum_ms': 14.074}
W counts: {"admitted": 29970, "audit_enqueued": 59396, "audit_written": 59396, "background_round_trips": 42926, "disposition_ALLOW": 29436, "disposition_BLOCK": 534, "guard_windows": 49429, "lease_refills": 136, "provider_calls": 29436, "provider_connections_opened": 4689, "requests_by_round_trips{n=\"0\"}": 29834, "requests_by_round_trips{n=\"1\"}": 136, "shared_state_round_trips": 136}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 0.132}, "per_proc_util": {"nginx": [0.0, 0.004, 0.006, 0.007, 0.007, 0.007, 0.008, 0.008, 0.009, 0.009, 0.009, 0.009, 0.01, 0.01, 0.01, 0.01, 0.011]}, "per_core_util": {"max": 0.009, "mean": 0.008, "sum": 0.14, "n": 16}, "nic": null, "restarted": [], "loadavg_max": 0.14} nginx cpu-ms/req 1.32
redis: ops/s 794.6 ops/req 7.946 cpu cores 0.007 clients 361 mem 641.6MB ping(us) {'n': 28300, 'p50_us': 501.0, 'p90_us': 525.7, 'p99_us': 614.2, 'p99.9_us': 1316.9, 'max_us': 3549.6, 'mean_us': 508.2}
redis cmdstats: {'info': {'calls_per_s': 0.0, 'usec_per_call': 116.0}, 'hgetall': {'calls_per_s': 357.9, 'usec_per_call': 0.21}, 'evalsha': {'calls_per_s': 0.5, 'usec_per_call': 11.32}, 'xadd': {'calls_per_s': 197.9, 'usec_per_call': 3.56}, 'decrby': {'calls_per_s': 0.5, 'usec_per_call': 0.4}, 'mget': {'calls_per_s': 143.1, 'usec_per_call': 0.35}, 'get': {'calls_per_s': 0.5, 'usec_per_call': 0.57}, 'hello': {'calls_per_s': 0.1, 'usec_per_call': 2.94}, 'ping': {'calls_per_s': 94.2, 'usec_per_call': 0.09}}
wire: {'requests_in_window': 30000, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56230.5, 'client_side_to_unit': 9864.6, 'unit_to_provider': 10378.1, 'provider_to_unit': 56919.2, 'unit_to_redis': 5901.9, 'redis_to_unit': 503.7}, 'edge_ip_bytes_per_req': {'edge_to_clients': 56341.1, 'clients_to_edge': 8326.9}, 'olg_resp_body_bytes_mean': {'sse': 67574.8, 'json': 1425.3}}
