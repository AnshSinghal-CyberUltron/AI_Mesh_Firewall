# f4u-140: strict FAIL | load-knee FAIL (sut, units=4, rate=140)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 42000 (140.0/s) qualified 40945 (136.48/s) FP-blocks 752 (0.0179) infra 303 (0.007214285714285714) drops 0 safety 0
infra reasons: {'http_503': 303, 'incomplete': 303, 'unjoined': 303, 'disposition_missing': 303, 'stage_canon_missing': 303, 'stage_det_missing': 303, 'stage_sem_missing': 303, 'stage_resolve_missing': 303, 'stage_dispatch_missing': 303, 'stage_out_missing': 303, 'stage_audit_missing': 303}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 303}

T_fw_addon: n=40945 p50=10.3363 p90=14.9111 p99=53.3075 p99.9=71.3457 max=90.537 mean=12.6719
T_fw_addon_nohold: n=40945 p50=10.1086 p90=12.868 p99=15.5529 p99.9=19.9101 max=35.6767 mean=9.57
T_fw_addon_sse: n=28677 p50=10.4247 p90=33.2634 p99=53.9969 p99.9=72.3337 max=90.537 mean=13.9774
T_fw_addon_json: n=12268 p50=10.1357 p90=13.0008 p99=15.4621 p99.9=19.9641 max=31.2743 mean=9.6202
T_addon_first_sse: n=28677 p50=10.035 p90=13.2339 p99=28.8549 p99.9=34.0215 max=54.2087 mean=9.7746
T_addon_total_sse: n=28677 p50=10.0974 p90=12.8047 p99=15.6329 p99.9=19.8063 max=35.6767 mean=9.5485
T_addon_total_json: n=12268 p50=10.1357 p90=13.0008 p99=15.4621 p99.9=19.9641 max=31.2743 mean=9.6202
T_release_lag_max: n=2947 p50=49.9367 p90=53.9446 p99=72.2981 p99.9=86.6723 max=90.537 mean=49.5389
lateness: n=42000 p50=0.0877 p90=0.0976 p99=0.1087 p99.9=0.1209 max=0.2407 mean=0.0873
loadgen: [{'vm': 'rv-pbf-lg-1', 'busy_max': 5.4, 'busy_mean': 4.9, 'late_max_us': 373, 'conn_opens': 247, 'max_inflight': 239}, {'vm': 'rv-pbf-lg-2', 'busy_max': 5.4, 'busy_mean': 4.9, 'late_max_us': 209, 'conn_opens': 253, 'max_inflight': 241}, {'vm': 'rv-pbf-lg-3', 'busy_max': 5.3, 'busy_mean': 4.9, 'late_max_us': 240, 'conn_opens': 253, 'max_inflight': 241}]  provider_cpu_busy_max: 14.759494416658292
gateway cores total 5.15 cpu-ms/req {'gateway': 36.873, 'workers': 32.795, 'owners': 4.066}
  rv-pbf-unit-2: cores {'launcher': 0.0, 'owner': 0.142, 'owner0': 0.077, 'owner1': 0.064, 'redis': 0.001, 'worker': 1.157} worker util max 0.082 per-core max 0.068 mean 0.057 gpu {'0': {'n': 298, 'sm_mean': 5.7, 'sm_p95': 11.0, 'sm_max': 17.0}, '1': {'n': 298, 'sm_mean': 4.9, 'sm_p95': 11.0, 'sm_max': 18.0}} t_input_p99 12.6484 per-worker admitted {'n': 18, 'min': 239, 'max': 794, 'mean': 582.7, 'max_over_mean': 1.363, 'sheds_per_worker': [0, 1, 1, 1, 1, 2, 2, 3, 3, 4, 4, 4, 5, 6, 6, 7, 8, 10]}
  rv-pbf-unit-3: cores {'launcher': 0.0, 'owner': 0.141, 'owner0': 0.056, 'owner1': 0.086, 'redis': 0.001, 'worker': 1.135} worker util max 0.107 per-core max 0.066 mean 0.055 gpu {'0': {'n': 298, 'sm_mean': 4.1, 'sm_p95': 11.0, 'sm_max': 15.0}, '1': {'n': 298, 'sm_mean': 6.9, 'sm_p95': 15.0, 'sm_max': 20.0}} t_input_p99 12.5174 per-worker admitted {'n': 18, 'min': 172, 'max': 1079, 'mean': 582.6, 'max_over_mean': 1.852, 'sheds_per_worker': [0, 0, 0, 1, 1, 2, 3, 3, 4, 6, 6, 6, 7, 7, 7, 8, 10, 10]}
  rv-pbf-unit-4: cores {'launcher': 0.0, 'owner': 0.144, 'owner0': 0.074, 'owner1': 0.071, 'redis': 0.001, 'worker': 1.165} worker util max 0.083 per-core max 0.063 mean 0.057 gpu {'0': {'n': 298, 'sm_mean': 5.8, 'sm_p95': 13.0, 'sm_max': 20.0}, '1': {'n': 298, 'sm_mean': 4.9, 'sm_p95': 12.0, 'sm_max': 17.0}} t_input_p99 12.6484 per-worker admitted {'n': 18, 'min': 357, 'max': 796, 'mean': 583.2, 'max_over_mean': 1.365, 'sheds_per_worker': [0, 0, 1, 1, 2, 2, 2, 3, 3, 4, 4, 6, 6, 6, 8, 8, 9, 10]}
  rv-pbf-unit-5: cores {'launcher': 0.0, 'owner': 0.14, 'owner0': 0.073, 'owner1': 0.067, 'redis': 0.001, 'worker': 1.12} worker util max 0.089 per-core max 0.068 mean 0.055 gpu {'0': {'n': 298, 'sm_mean': 5.3, 'sm_p95': 11.0, 'sm_max': 23.0}, '1': {'n': 298, 'sm_mean': 5.4, 'sm_p95': 12.0, 'sm_max': 17.0}} t_input_p99 12.3863 per-worker admitted {'n': 18, 'min': 308, 'max': 909, 'mean': 583.3, 'max_over_mean': 1.558, 'sheds_per_worker': [0, 1, 1, 2, 3, 3, 3, 3, 4, 4, 4, 5, 5, 6, 7, 9, 10, 10]}
W t_input_ns: {'n': 41668, 'mean_ms': 7.4297, 'p50_ms': 8.1592, 'p90_ms': 10.027, 'p99_ms': 12.5174, 'p99.9_ms': 16.1874, 'max_cum_ms': 36.429}
W t_tokenize_ns: {'n': 41972, 'mean_ms': 2.6979, 'p50_ms': 2.7361, 'p90_ms': 4.1452, 'p99_ms': 4.7514, 'p99.9_ms': 6.0621, 'max_cum_ms': 8.9355}
W t_guard_wait_ns: {'n': 41668, 'mean_ms': 4.2006, 'p50_ms': 4.7514, 'p90_ms': 5.2101, 'p99_ms': 7.3073, 'p99.9_ms': 11.4688, 'max_cum_ms': 30.0756}
W guard_owner_rtt_ns: {'n': 41668, 'mean_ms': 4.3697, 'p50_ms': 4.948, 'p90_ms': 5.4723, 'p99_ms': 7.5694, 'p99.9_ms': 10.9445, 'max_cum_ms': 28.7454}
W guard_queue_ns: {'n': 41668, 'mean_ms': 0.1118, 'p50_ms': 0.106, 'p90_ms': 0.1285, 'p99_ms': 0.1628, 'p99.9_ms': 1.8596, 'max_cum_ms': 16.1001}
W guard_exec_ns: {'n': 41668, 'mean_ms': 3.582, 'p50_ms': 4.2271, 'p90_ms': 4.4892, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 9.1328}
W t_admit_ns: {'n': 41972, 'mean_ms': 0.0928, 'p50_ms': 0.0876, 'p90_ms': 0.1039, 'p99_ms': 0.1306, 'p99.9_ms': 1.0117, 'max_cum_ms': 21.7487}
W release_processing_ns: {'n': 6383120, 'mean_ms': 0.063, 'p50_ms': 0.0627, 'p90_ms': 0.0783, 'p99_ms': 0.1009, 'p99.9_ms': 0.1285, 'max_cum_ms': 26.8838}
W loop_lag_ns: {'n': 214530, 'mean_ms': 0.7057, 'p50_ms': 0.0094, 'p90_ms': 1.3517, 'p99_ms': 7.1107, 'p99.9_ms': 8.8474, 'max_cum_ms': 27.862}
W audit_batch_write_ns: {'n': 82542, 'mean_ms': 0.9479, 'p50_ms': 0.9052, 'p90_ms': 1.1059, 'p99_ms': 1.3844, 'p99.9_ms': 7.3073, 'max_cum_ms': 40.9418}
W counts: {"admitted": 41972, "audit_enqueued": 82560, "audit_written": 82561, "background_round_trips": 42906, "disposition_ALLOW": 40916, "disposition_BLOCK": 752, "guard_windows": 68601, "lease_refills": 196, "provider_calls": 40916, "provider_connections_opened": 6144, "requests_by_round_trips{n=\"0\"}": 41776, "requests_by_round_trips{n=\"1\"}": 196, "shared_state_round_trips": 196, "shed{reason=\"guard_queue\"}": 304}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 0.172}, "per_proc_util": {"nginx": [0.0, 0.008, 0.009, 0.009, 0.009, 0.009, 0.009, 0.01, 0.01, 0.011, 0.011, 0.011, 0.012, 0.012, 0.013, 0.014, 0.015]}, "per_core_util": {"max": 0.012, "mean": 0.011, "sum": 0.18, "n": 16}, "nic": null, "restarted": [], "loadavg_max": 0.38} nginx cpu-ms/req 1.233
redis: ops/s 872.0 ops/req 6.228 cpu cores 0.008 clients 363 mem 613.7MB ping(us) {'n': 28343, 'p50_us': 481.0, 'p90_us': 512.6, 'p99_us': 617.9, 'p99.9_us': 1740.8, 'max_us': 4651.1, 'mean_us': 493.4}
redis cmdstats: {'info': {'calls_per_s': 0.0, 'usec_per_call': 128.0}, 'ping': {'calls_per_s': 94.3, 'usec_per_call': 0.1}, 'evalsha': {'calls_per_s': 0.7, 'usec_per_call': 10.93}, 'xadd': {'calls_per_s': 275.1, 'usec_per_call': 3.63}, 'mget': {'calls_per_s': 143.0, 'usec_per_call': 0.4}, 'hgetall': {'calls_per_s': 357.6, 'usec_per_call': 0.22}, 'get': {'calls_per_s': 0.7, 'usec_per_call': 0.63}, 'decrby': {'calls_per_s': 0.7, 'usec_per_call': 0.37}}
wire: {'requests_in_window': 42000, 'unit_ip_bytes_per_req': {'unit_to_client_side': 55895.1, 'client_side_to_unit': 9636.7, 'unit_to_provider': 10200.7, 'provider_to_unit': 56577.6, 'unit_to_redis': 5715.4, 'redis_to_unit': 428.7}, 'edge_ip_bytes_per_req': {'edge_to_clients': 56010.3, 'clients_to_edge': 8104.2}, 'olg_resp_body_bytes_mean': {'sse': 67605.7, 'json': 1425.1}}
