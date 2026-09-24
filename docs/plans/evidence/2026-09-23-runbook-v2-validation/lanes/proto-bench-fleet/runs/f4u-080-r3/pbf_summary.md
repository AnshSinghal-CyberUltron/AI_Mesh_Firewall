# f4u-080-r3: strict FAIL | load-knee PASS (sut, units=4, rate=80)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 24000 (80.0/s) qualified 23618 (78.73/s) FP-blocks 379 (0.01579) infra 3 (0.000125) drops 0 safety 0
infra reasons: {'http_503': 3, 'incomplete': 3, 'unjoined': 3, 'disposition_missing': 3, 'stage_canon_missing': 3, 'stage_det_missing': 3, 'stage_sem_missing': 3, 'stage_resolve_missing': 3, 'stage_dispatch_missing': 3, 'stage_out_missing': 3, 'stage_audit_missing': 3}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 3}

T_fw_addon: n=23618 p50=10.345 p90=14.5289 p99=52.9341 p99.9=70.9498 max=90.4253 mean=12.5073
T_fw_addon_nohold: n=23618 p50=10.1466 p90=12.7527 p99=15.3438 p99.9=19.7367 max=30.9139 mean=9.5619
T_fw_addon_sse: n=16530 p50=10.3929 p90=30.9139 p99=53.7054 p99.9=71.3449 max=90.4253 mean=13.7338
T_fw_addon_json: n=7088 p50=10.2251 p90=12.887 p99=15.5884 p99.9=19.7945 max=21.0186 mean=9.647
T_addon_first_sse: n=16530 p50=10.0548 p90=13.2302 p99=27.8691 p99.9=33.6003 max=55.0033 mean=9.7472
T_addon_total_sse: n=16530 p50=10.1183 p90=12.6933 p99=15.2958 p99.9=19.6581 max=30.9139 mean=9.5254
T_addon_total_json: n=7088 p50=10.2251 p90=12.887 p99=15.5884 p99.9=19.7945 max=21.0186 mean=9.647
T_release_lag_max: n=1622 p50=49.8539 p90=53.7056 p99=71.3449 p99.9=76.428 max=90.4253 mean=49.3062
lateness: n=24000 p50=0.0878 p90=0.0982 p99=0.1085 p99.9=0.1221 max=0.2225 mean=0.0875
loadgen: [{'vm': 'rv-pbf-lg-1', 'busy_max': 17.9, 'busy_mean': 4.3, 'late_max_us': 428, 'conn_opens': 151, 'max_inflight': 146}, {'vm': 'rv-pbf-lg-2', 'busy_max': 17.9, 'busy_mean': 4.2, 'late_max_us': 176, 'conn_opens': 151, 'max_inflight': 146}, {'vm': 'rv-pbf-lg-3', 'busy_max': 16.0, 'busy_mean': 4.3, 'late_max_us': 237, 'conn_opens': 151, 'max_inflight': 146}]  provider_cpu_busy_max: 17.222746078914277
gateway cores total 3.28 cpu-ms/req {'gateway': 41.101, 'workers': 36.844, 'owners': 4.236}
  rv-pbf-unit-2: cores {'launcher': 0.0, 'owner': 0.084, 'owner0': 0.047, 'owner1': 0.037, 'redis': 0.001, 'worker': 0.733} worker util max 0.064 per-core max 0.043 mean 0.037 gpu {'0': {'n': 299, 'sm_mean': 3.4, 'sm_p95': 9.0, 'sm_max': 18.0}, '1': {'n': 299, 'sm_mean': 2.9, 'sm_p95': 7.0, 'sm_max': 13.0}} t_input_p99 12.5174 per-worker admitted {'n': 18, 'min': 155, 'max': 596, 'mean': 333.2, 'max_over_mean': 1.789, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
  rv-pbf-unit-3: cores {'launcher': 0.0, 'owner': 0.083, 'owner0': 0.043, 'owner1': 0.041, 'redis': 0.001, 'worker': 0.725} worker util max 0.059 per-core max 0.05 mean 0.036 gpu {'0': {'n': 298, 'sm_mean': 3.0, 'sm_p95': 9.0, 'sm_max': 13.0}, '1': {'n': 298, 'sm_mean': 2.8, 'sm_p95': 7.0, 'sm_max': 13.0}} t_input_p99 12.2552 per-worker admitted {'n': 18, 'min': 66, 'max': 534, 'mean': 332.9, 'max_over_mean': 1.604, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1]}
  rv-pbf-unit-4: cores {'launcher': 0.0, 'owner': 0.085, 'owner0': 0.039, 'owner1': 0.046, 'redis': 0.001, 'worker': 0.741} worker util max 0.075 per-core max 0.052 mean 0.037 gpu {'0': {'n': 298, 'sm_mean': 2.8, 'sm_p95': 7.0, 'sm_max': 11.0}, '1': {'n': 298, 'sm_mean': 3.3, 'sm_p95': 10.0, 'sm_max': 14.0}} t_input_p99 12.3863 per-worker admitted {'n': 18, 'min': 150, 'max': 694, 'mean': 333.1, 'max_over_mean': 2.084, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
  rv-pbf-unit-5: cores {'launcher': 0.0, 'owner': 0.085, 'owner0': 0.039, 'owner1': 0.047, 'redis': 0.001, 'worker': 0.739} worker util max 0.06 per-core max 0.046 mean 0.037 gpu {'0': {'n': 298, 'sm_mean': 2.8, 'sm_p95': 9.0, 'sm_max': 16.0}, '1': {'n': 298, 'sm_mean': 3.5, 'sm_p95': 9.0, 'sm_max': 16.0}} t_input_p99 12.3863 per-worker admitted {'n': 18, 'min': 124, 'max': 556, 'mean': 333.3, 'max_over_mean': 1.668, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1]}
W t_input_ns: {'n': 23983, 'mean_ms': 7.4513, 'p50_ms': 8.1592, 'p90_ms': 9.8959, 'p99_ms': 12.3863, 'p99.9_ms': 15.9252, 'max_cum_ms': 36.429}
W t_tokenize_ns: {'n': 23985, 'mean_ms': 2.6544, 'p50_ms': 2.6706, 'p90_ms': 4.0468, 'p99_ms': 4.5548, 'p99.9_ms': 5.9965, 'max_cum_ms': 8.9355}
W t_guard_wait_ns: {'n': 23983, 'mean_ms': 4.2598, 'p50_ms': 4.8169, 'p90_ms': 5.2101, 'p99_ms': 7.3073, 'p99.9_ms': 11.2067, 'max_cum_ms': 30.0756}
W guard_owner_rtt_ns: {'n': 23983, 'mean_ms': 4.432, 'p50_ms': 5.0135, 'p90_ms': 5.4723, 'p99_ms': 7.5694, 'p99.9_ms': 10.4202, 'max_cum_ms': 28.7454}
W guard_queue_ns: {'n': 23983, 'mean_ms': 0.1117, 'p50_ms': 0.1111, 'p90_ms': 0.1306, 'p99_ms': 0.1546, 'p99.9_ms': 0.2161, 'max_cum_ms': 16.1001}
W guard_exec_ns: {'n': 23983, 'mean_ms': 3.6382, 'p50_ms': 4.2271, 'p90_ms': 4.5548, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 9.1328}
W t_admit_ns: {'n': 23985, 'mean_ms': 0.095, 'p50_ms': 0.0896, 'p90_ms': 0.1029, 'p99_ms': 0.1321, 'p99.9_ms': 1.0363, 'max_cum_ms': 21.7487}
W release_processing_ns: {'n': 3664677, 'mean_ms': 0.0654, 'p50_ms': 0.0648, 'p90_ms': 0.0804, 'p99_ms': 0.105, 'p99.9_ms': 0.1403, 'max_cum_ms': 26.8838}
W loop_lag_ns: {'n': 214550, 'mean_ms': 0.6965, 'p50_ms': 0.0122, 'p90_ms': 1.1223, 'p99_ms': 6.914, 'p99.9_ms': 8.0282, 'max_cum_ms': 27.862}
W audit_batch_write_ns: {'n': 47572, 'mean_ms': 0.9742, 'p50_ms': 0.9298, 'p90_ms': 1.1551, 'p99_ms': 1.3844, 'p99.9_ms': 7.5039, 'max_cum_ms': 40.9418}
W counts: {"admitted": 23985, "audit_enqueued": 47578, "audit_written": 47578, "background_round_trips": 42910, "disposition_ALLOW": 23604, "disposition_BLOCK": 379, "guard_windows": 39586, "lease_refills": 107, "provider_calls": 23604, "provider_connections_opened": 2865, "requests_by_round_trips{n=\"0\"}": 23878, "requests_by_round_trips{n=\"1\"}": 107, "shared_state_round_trips": 107, "shed{reason=\"guard_queue\"}": 3}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 0.092}, "per_proc_util": {"nginx": [0.0, 0.004, 0.004, 0.005, 0.005, 0.005, 0.005, 0.005, 0.006, 0.006, 0.006, 0.006, 0.007, 0.007, 0.007, 0.007, 0.008]}, "per_core_util": {"max": 0.008, "mean": 0.006, "sum": 0.1, "n": 16}, "nic": null, "restarted": [], "loadavg_max": 0.2} nginx cpu-ms/req 1.159
redis: ops/s 753.9 ops/req 9.424 cpu cores 0.006 clients 363 mem 1551.1MB ping(us) {'n': 28155, 'p50_us': 549.4, 'p90_us': 591.7, 'p99_us': 706.3, 'p99.9_us': 1706.8, 'max_us': 4364.9, 'mean_us': 563.4}
redis cmdstats: {'info': {'calls_per_s': 0.0, 'usec_per_call': 153.0}, 'ping': {'calls_per_s': 93.7, 'usec_per_call': 0.1}, 'evalsha': {'calls_per_s': 0.4, 'usec_per_call': 15.53}, 'xadd': {'calls_per_s': 158.5, 'usec_per_call': 3.73}, 'mget': {'calls_per_s': 143.0, 'usec_per_call': 0.39}, 'hgetall': {'calls_per_s': 357.6, 'usec_per_call': 0.22}, 'get': {'calls_per_s': 0.4, 'usec_per_call': 0.81}, 'decrby': {'calls_per_s': 0.4, 'usec_per_call': 0.53}}
wire: {'requests_in_window': 24000, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56156.2, 'client_side_to_unit': 9928.9, 'unit_to_provider': 10386.4, 'provider_to_unit': 56843.6, 'unit_to_redis': 6035.6, 'redis_to_unit': 566.4}, 'edge_ip_bytes_per_req': {'edge_to_clients': 56275.2, 'clients_to_edge': 8329.2}, 'olg_resp_body_bytes_mean': {'sse': 67306.3, 'json': 1433.5}}
