# f4u-080: strict FAIL | load-knee PASS (sut, units=4, rate=80)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 24000 (80.0/s) qualified 23614 (78.71/s) FP-blocks 386 (0.01608) infra 0 (0.0) drops 0 safety 0
infra reasons: {}
infra detail: {}

T_fw_addon: n=23614 p50=10.2549 p90=14.4424 p99=52.602 p99.9=70.6764 max=93.5355 mean=12.4421
T_fw_addon_nohold: n=23614 p50=10.0536 p90=12.6019 p99=15.128 p99.9=18.513 max=32.6091 mean=9.4795
T_fw_addon_sse: n=16529 p50=10.3088 p90=30.8235 p99=53.6057 p99.9=70.9609 max=93.5355 mean=13.6754
T_fw_addon_json: n=7085 p50=10.1319 p90=12.8232 p99=15.2504 p99.9=18.513 max=32.6091 mean=9.5647
T_addon_first_sse: n=16529 p50=9.9579 p90=13.1802 p99=29.5742 p99.9=33.6659 max=50.1939 mean=9.6751
T_addon_total_sse: n=16529 p50=10.0294 p90=12.523 p99=15.0725 p99.9=18.3728 max=30.1519 mean=9.443
T_addon_total_json: n=7085 p50=10.1319 p90=12.8232 p99=15.2504 p99.9=18.513 max=32.6091 mean=9.5647
T_release_lag_max: n=1626 p50=49.8335 p90=53.6256 p99=70.9609 p99.9=86.7706 max=93.5355 mean=49.0453
lateness: n=24000 p50=0.0867 p90=0.0972 p99=0.1079 p99.9=0.1215 max=0.1822 mean=0.0871
loadgen: [{'vm': 'rv-pbf-lg-1', 'busy_max': 16.0, 'busy_mean': 4.3, 'late_max_us': 328, 'conn_opens': 151, 'max_inflight': 146}, {'vm': 'rv-pbf-lg-2', 'busy_max': 16.4, 'busy_mean': 4.2, 'late_max_us': 150, 'conn_opens': 152, 'max_inflight': 147}, {'vm': 'rv-pbf-lg-3', 'busy_max': 18.0, 'busy_mean': 4.3, 'late_max_us': 230, 'conn_opens': 150, 'max_inflight': 146}]  provider_cpu_busy_max: 19.244888988894537
gateway cores total 3.24 cpu-ms/req {'gateway': 40.582, 'workers': 36.364, 'owners': 4.197}
  rv-pbf-unit-2: cores {'launcher': 0.0, 'owner': 0.084, 'owner0': 0.046, 'owner1': 0.038, 'redis': 0.001, 'worker': 0.731} worker util max 0.058 per-core max 0.054 mean 0.036 gpu {'0': {'n': 298, 'sm_mean': 3.6, 'sm_p95': 9.0, 'sm_max': 17.0}, '1': {'n': 298, 'sm_mean': 2.8, 'sm_p95': 7.0, 'sm_max': 13.0}} t_input_p99 12.3863 per-worker admitted {'n': 18, 'min': 123, 'max': 522, 'mean': 333.2, 'max_over_mean': 1.567, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
  rv-pbf-unit-3: cores {'launcher': 0.0, 'owner': 0.084, 'owner0': 0.037, 'owner1': 0.047, 'redis': 0.001, 'worker': 0.712} worker util max 0.059 per-core max 0.05 mean 0.036 gpu {'0': {'n': 298, 'sm_mean': 2.6, 'sm_p95': 7.0, 'sm_max': 13.0}, '1': {'n': 298, 'sm_mean': 3.8, 'sm_p95': 11.0, 'sm_max': 18.0}} t_input_p99 12.2552 per-worker admitted {'n': 18, 'min': 140, 'max': 549, 'mean': 332.9, 'max_over_mean': 1.649, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
  rv-pbf-unit-4: cores {'launcher': 0.0, 'owner': 0.084, 'owner0': 0.04, 'owner1': 0.044, 'redis': 0.001, 'worker': 0.745} worker util max 0.056 per-core max 0.046 mean 0.037 gpu {'0': {'n': 298, 'sm_mean': 2.8, 'sm_p95': 7.0, 'sm_max': 11.0}, '1': {'n': 298, 'sm_mean': 3.2, 'sm_p95': 9.0, 'sm_max': 17.0}} t_input_p99 12.3863 per-worker admitted {'n': 18, 'min': 152, 'max': 495, 'mean': 332.9, 'max_over_mean': 1.487, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
  rv-pbf-unit-5: cores {'launcher': 0.0, 'owner': 0.082, 'owner0': 0.036, 'owner1': 0.046, 'redis': 0.001, 'worker': 0.711} worker util max 0.054 per-core max 0.046 mean 0.036 gpu {'0': {'n': 298, 'sm_mean': 2.7, 'sm_p95': 8.0, 'sm_max': 15.0}, '1': {'n': 298, 'sm_mean': 3.3, 'sm_p95': 9.0, 'sm_max': 15.0}} t_input_p99 12.1242 per-worker admitted {'n': 18, 'min': 196, 'max': 496, 'mean': 333.2, 'max_over_mean': 1.489, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
W t_input_ns: {'n': 23980, 'mean_ms': 7.3905, 'p50_ms': 8.0937, 'p90_ms': 9.8959, 'p99_ms': 12.2552, 'p99.9_ms': 15.532, 'max_cum_ms': 36.429}
W t_tokenize_ns: {'n': 23979, 'mean_ms': 2.6452, 'p50_ms': 2.6706, 'p90_ms': 4.0468, 'p99_ms': 4.5548, 'p99.9_ms': 5.931, 'max_cum_ms': 8.9355}
W t_guard_wait_ns: {'n': 23980, 'mean_ms': 4.2193, 'p50_ms': 4.7514, 'p90_ms': 5.2101, 'p99_ms': 7.3073, 'p99.9_ms': 11.0756, 'max_cum_ms': 30.0756}
W guard_owner_rtt_ns: {'n': 23980, 'mean_ms': 4.3895, 'p50_ms': 4.948, 'p90_ms': 5.4067, 'p99_ms': 7.5039, 'p99.9_ms': 10.5513, 'max_cum_ms': 28.7454}
W guard_queue_ns: {'n': 23980, 'mean_ms': 0.1092, 'p50_ms': 0.1091, 'p90_ms': 0.1275, 'p99_ms': 0.1526, 'p99.9_ms': 0.8479, 'max_cum_ms': 16.1001}
W guard_exec_ns: {'n': 23980, 'mean_ms': 3.6123, 'p50_ms': 4.2271, 'p90_ms': 4.4892, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 9.1328}
W t_admit_ns: {'n': 23979, 'mean_ms': 0.0943, 'p50_ms': 0.0886, 'p90_ms': 0.1039, 'p99_ms': 0.1295, 'p99.9_ms': 1.0117, 'max_cum_ms': 21.7487}
W release_processing_ns: {'n': 3664841, 'mean_ms': 0.0643, 'p50_ms': 0.0643, 'p90_ms': 0.0783, 'p99_ms': 0.1019, 'p99.9_ms': 0.1382, 'max_cum_ms': 26.8838}
W loop_lag_ns: {'n': 214550, 'mean_ms': 0.6924, 'p50_ms': 0.0092, 'p90_ms': 1.1223, 'p99_ms': 6.914, 'p99.9_ms': 8.1592, 'max_cum_ms': 27.862}
W audit_batch_write_ns: {'n': 47566, 'mean_ms': 0.9634, 'p50_ms': 0.9134, 'p90_ms': 1.1387, 'p99_ms': 1.3681, 'p99.9_ms': 7.3728, 'max_cum_ms': 40.9418}
W counts: {"admitted": 23979, "audit_enqueued": 47571, "audit_written": 47571, "background_round_trips": 42910, "disposition_ALLOW": 23594, "disposition_BLOCK": 386, "guard_windows": 39584, "lease_refills": 113, "provider_calls": 23594, "provider_connections_opened": 2857, "requests_by_round_trips{n=\"0\"}": 23866, "requests_by_round_trips{n=\"1\"}": 113, "shared_state_round_trips": 113}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 0.092}, "per_proc_util": {"nginx": [0.0, 0.004, 0.005, 0.005, 0.005, 0.005, 0.005, 0.005, 0.006, 0.006, 0.006, 0.006, 0.006, 0.006, 0.007, 0.007, 0.008]}, "per_core_util": {"max": 0.008, "mean": 0.006, "sum": 0.1, "n": 16}, "nic": null, "restarted": [], "loadavg_max": 0.14} nginx cpu-ms/req 1.15
redis: ops/s 754.6 ops/req 9.432 cpu cores 0.006 clients 363 mem 1224.7MB ping(us) {'n': 28319, 'p50_us': 488.5, 'p90_us': 520.9, 'p99_us': 630.4, 'p99.9_us': 1703.7, 'max_us': 4828.1, 'mean_us': 501.9}
redis cmdstats: {'info': {'calls_per_s': 0.0, 'usec_per_call': 140.0}, 'ping': {'calls_per_s': 94.2, 'usec_per_call': 0.1}, 'evalsha': {'calls_per_s': 0.4, 'usec_per_call': 11.56}, 'xadd': {'calls_per_s': 158.5, 'usec_per_call': 3.6}, 'mget': {'calls_per_s': 143.0, 'usec_per_call': 0.39}, 'hgetall': {'calls_per_s': 357.6, 'usec_per_call': 0.22}, 'get': {'calls_per_s': 0.4, 'usec_per_call': 0.65}, 'decrby': {'calls_per_s': 0.4, 'usec_per_call': 0.35}}
wire: {'requests_in_window': 24000, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56151.7, 'client_side_to_unit': 9865.4, 'unit_to_provider': 10349.3, 'provider_to_unit': 56836.4, 'unit_to_redis': 6036.2, 'redis_to_unit': 566.9}, 'edge_ip_bytes_per_req': {'edge_to_clients': 56269.8, 'clients_to_edge': 8346.2}, 'olg_resp_body_bytes_mean': {'sse': 67303.7, 'json': 1433.1}}
