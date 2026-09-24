# f4u-080-r2: strict FAIL | load-knee PASS (sut, units=4, rate=80)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 24000 (80.0/s) qualified 23617 (78.72/s) FP-blocks 383 (0.01596) infra 0 (0.0) drops 0 safety 0
infra reasons: {}
infra detail: {}

T_fw_addon: n=23617 p50=10.3298 p90=14.7075 p99=52.8976 p99.9=70.7458 max=75.2456 mean=12.5697
T_fw_addon_nohold: n=23617 p50=10.1238 p90=12.7418 p99=15.4308 p99.9=18.9912 max=22.795 mean=9.5464
T_fw_addon_sse: n=16530 p50=10.3926 p90=31.4381 p99=53.7424 p99.9=70.9863 max=75.2456 mean=13.8313
T_fw_addon_json: n=7087 p50=10.1756 p90=12.9546 p99=15.5112 p99.9=18.9513 max=21.8908 mean=9.6271
T_addon_first_sse: n=16530 p50=10.0335 p90=13.2587 p99=29.6258 p99.9=33.9491 max=53.2615 mean=9.7732
T_addon_total_sse: n=16530 p50=10.1005 p90=12.624 p99=15.3942 p99.9=18.9999 max=22.795 mean=9.5118
T_addon_total_json: n=7087 p50=10.1756 p90=12.9546 p99=15.5112 p99.9=18.9513 max=21.8908 mean=9.6271
T_release_lag_max: n=1662 p50=49.7999 p90=53.7199 p99=70.9863 p99.9=74.6549 max=75.2456 mean=49.132
lateness: n=24000 p50=0.0879 p90=0.0977 p99=0.1088 p99.9=0.1256 max=0.3315 mean=0.0871
loadgen: [{'vm': 'rv-pbf-lg-1', 'busy_max': 17.9, 'busy_mean': 4.3, 'late_max_us': 368, 'conn_opens': 151, 'max_inflight': 146}, {'vm': 'rv-pbf-lg-2', 'busy_max': 17.9, 'busy_mean': 4.2, 'late_max_us': 331, 'conn_opens': 152, 'max_inflight': 147}, {'vm': 'rv-pbf-lg-3', 'busy_max': 13.1, 'busy_mean': 4.2, 'late_max_us': 249, 'conn_opens': 151, 'max_inflight': 146}]  provider_cpu_busy_max: 21.847455928140004
gateway cores total 3.28 cpu-ms/req {'gateway': 41.128, 'workers': 36.874, 'owners': 4.232}
  rv-pbf-unit-2: cores {'launcher': 0.0, 'owner': 0.086, 'owner0': 0.048, 'owner1': 0.038, 'redis': 0.001, 'worker': 0.766} worker util max 0.062 per-core max 0.046 mean 0.039 gpu {'0': {'n': 298, 'sm_mean': 3.1, 'sm_p95': 7.0, 'sm_max': 15.0}, '1': {'n': 298, 'sm_mean': 2.8, 'sm_p95': 7.0, 'sm_max': 13.0}} t_input_p99 12.6484 per-worker admitted {'n': 18, 'min': 162, 'max': 544, 'mean': 333.2, 'max_over_mean': 1.633, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
  rv-pbf-unit-3: cores {'launcher': 0.0, 'owner': 0.083, 'owner0': 0.036, 'owner1': 0.047, 'redis': 0.001, 'worker': 0.712} worker util max 0.058 per-core max 0.045 mean 0.036 gpu {'0': {'n': 299, 'sm_mean': 2.4, 'sm_p95': 7.0, 'sm_max': 11.0}, '1': {'n': 299, 'sm_mean': 3.7, 'sm_p95': 9.0, 'sm_max': 18.0}} t_input_p99 12.2552 per-worker admitted {'n': 18, 'min': 82, 'max': 529, 'mean': 333.1, 'max_over_mean': 1.588, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
  rv-pbf-unit-4: cores {'launcher': 0.0, 'owner': 0.085, 'owner0': 0.039, 'owner1': 0.046, 'redis': 0.001, 'worker': 0.746} worker util max 0.056 per-core max 0.044 mean 0.038 gpu {'0': {'n': 298, 'sm_mean': 2.6, 'sm_p95': 7.0, 'sm_max': 15.0}, '1': {'n': 298, 'sm_mean': 3.3, 'sm_p95': 10.0, 'sm_max': 14.0}} t_input_p99 12.3863 per-worker admitted {'n': 18, 'min': 166, 'max': 485, 'mean': 332.8, 'max_over_mean': 1.457, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
  rv-pbf-unit-5: cores {'launcher': 0.0, 'owner': 0.083, 'owner0': 0.04, 'owner1': 0.043, 'redis': 0.001, 'worker': 0.716} worker util max 0.061 per-core max 0.043 mean 0.036 gpu {'0': {'n': 299, 'sm_mean': 3.0, 'sm_p95': 8.0, 'sm_max': 27.0}, '1': {'n': 299, 'sm_mean': 3.2, 'sm_p95': 9.0, 'sm_max': 15.0}} t_input_p99 12.2552 per-worker admitted {'n': 18, 'min': 193, 'max': 578, 'mean': 333.0, 'max_over_mean': 1.736, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
W t_input_ns: {'n': 23978, 'mean_ms': 7.4417, 'p50_ms': 8.1592, 'p90_ms': 9.8959, 'p99_ms': 12.3863, 'p99.9_ms': 15.6631, 'max_cum_ms': 36.429}
W t_tokenize_ns: {'n': 23978, 'mean_ms': 2.6517, 'p50_ms': 2.6706, 'p90_ms': 4.0468, 'p99_ms': 4.5548, 'p99.9_ms': 5.931, 'max_cum_ms': 8.9355}
W t_guard_wait_ns: {'n': 23978, 'mean_ms': 4.2552, 'p50_ms': 4.8169, 'p90_ms': 5.2756, 'p99_ms': 7.3073, 'p99.9_ms': 11.3377, 'max_cum_ms': 30.0756}
W guard_owner_rtt_ns: {'n': 23978, 'mean_ms': 4.4255, 'p50_ms': 4.948, 'p90_ms': 5.4723, 'p99_ms': 7.5694, 'p99.9_ms': 10.6824, 'max_cum_ms': 28.7454}
W guard_queue_ns: {'n': 23978, 'mean_ms': 0.1117, 'p50_ms': 0.1111, 'p90_ms': 0.1306, 'p99_ms': 0.1567, 'p99.9_ms': 0.4321, 'max_cum_ms': 16.1001}
W guard_exec_ns: {'n': 23978, 'mean_ms': 3.6357, 'p50_ms': 4.2271, 'p90_ms': 4.5548, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 9.1328}
W t_admit_ns: {'n': 23978, 'mean_ms': 0.0954, 'p50_ms': 0.0896, 'p90_ms': 0.106, 'p99_ms': 0.1321, 'p99.9_ms': 1.0117, 'max_cum_ms': 21.7487}
W release_processing_ns: {'n': 3666194, 'mean_ms': 0.0656, 'p50_ms': 0.0653, 'p90_ms': 0.0804, 'p99_ms': 0.105, 'p99.9_ms': 0.1403, 'max_cum_ms': 26.8838}
W loop_lag_ns: {'n': 214570, 'mean_ms': 0.6952, 'p50_ms': 0.0112, 'p90_ms': 1.1387, 'p99_ms': 6.914, 'p99.9_ms': 8.0937, 'max_cum_ms': 27.862}
W audit_batch_write_ns: {'n': 47577, 'mean_ms': 0.9641, 'p50_ms': 0.9216, 'p90_ms': 1.1387, 'p99_ms': 1.3517, 'p99.9_ms': 7.3728, 'max_cum_ms': 40.9418}
W counts: {"admitted": 23978, "audit_enqueued": 47582, "audit_written": 47581, "background_round_trips": 42914, "disposition_ALLOW": 23595, "disposition_BLOCK": 383, "guard_windows": 39586, "lease_refills": 115, "provider_calls": 23595, "provider_connections_opened": 2939, "requests_by_round_trips{n=\"0\"}": 23863, "requests_by_round_trips{n=\"1\"}": 115, "shared_state_round_trips": 115}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 0.091}, "per_proc_util": {"nginx": [0.0, 0.003, 0.004, 0.004, 0.005, 0.005, 0.005, 0.006, 0.006, 0.006, 0.006, 0.006, 0.006, 0.007, 0.007, 0.007, 0.007]}, "per_core_util": {"max": 0.008, "mean": 0.006, "sum": 0.1, "n": 16}, "nic": null, "restarted": [], "loadavg_max": 0.26} nginx cpu-ms/req 1.145
redis: ops/s 754.5 ops/req 9.431 cpu cores 0.006 clients 363 mem 1387.9MB ping(us) {'n': 28308, 'p50_us': 492.2, 'p90_us': 521.9, 'p99_us': 647.9, 'p99.9_us': 1756.9, 'max_us': 6165.4, 'mean_us': 505.5}
redis cmdstats: {'info': {'calls_per_s': 0.0, 'usec_per_call': 154.0}, 'ping': {'calls_per_s': 94.2, 'usec_per_call': 0.1}, 'evalsha': {'calls_per_s': 0.4, 'usec_per_call': 17.05}, 'xadd': {'calls_per_s': 158.5, 'usec_per_call': 3.75}, 'mget': {'calls_per_s': 143.0, 'usec_per_call': 0.4}, 'hgetall': {'calls_per_s': 357.6, 'usec_per_call': 0.22}, 'get': {'calls_per_s': 0.4, 'usec_per_call': 0.83}, 'decrby': {'calls_per_s': 0.4, 'usec_per_call': 0.54}}
wire: {'requests_in_window': 24000, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56162.8, 'client_side_to_unit': 9881.6, 'unit_to_provider': 10352.7, 'provider_to_unit': 56846.9, 'unit_to_redis': 6036.7, 'redis_to_unit': 566.9}, 'edge_ip_bytes_per_req': {'edge_to_clients': 56282.0, 'clients_to_edge': 8354.3}, 'olg_resp_body_bytes_mean': {'sse': 67313.6, 'json': 1433.8}}
