# f4u-120: strict FAIL | load-knee FAIL (sut, units=4, rate=120)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 36000 (120.0/s) qualified 35144 (117.15/s) FP-blocks 665 (0.01847) infra 191 (0.0053055555555555555) drops 0 safety 0
infra reasons: {'http_503': 191, 'incomplete': 191, 'unjoined': 191, 'disposition_missing': 191, 'stage_canon_missing': 191, 'stage_det_missing': 191, 'stage_sem_missing': 191, 'stage_resolve_missing': 191, 'stage_dispatch_missing': 191, 'stage_out_missing': 191, 'stage_audit_missing': 191}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 191}

T_fw_addon: n=35144 p50=10.4713 p90=14.9739 p99=53.1861 p99.9=71.3325 max=92.2669 mean=12.7638
T_fw_addon_nohold: n=35144 p50=10.2088 p90=13.1939 p99=15.7741 p99.9=19.8552 max=25.1821 mean=9.74
T_fw_addon_sse: n=24593 p50=10.5671 p90=31.4097 p99=54.0808 p99.9=71.7236 max=92.2669 mean=14.0295
T_fw_addon_json: n=10551 p50=10.2521 p90=13.3608 p99=15.9109 p99.9=19.8248 max=24.0555 mean=9.8134
T_addon_first_sse: n=24593 p50=10.1334 p90=13.4061 p99=27.669 p99.9=33.84 max=50.8325 mean=9.9153
T_addon_total_sse: n=24593 p50=10.1903 p90=13.0853 p99=15.7429 p99.9=19.9358 max=25.1821 mean=9.7085
T_addon_total_json: n=10551 p50=10.2521 p90=13.3608 p99=15.9109 p99.9=19.8248 max=24.0555 mean=9.8134
T_release_lag_max: n=2482 p50=49.8631 p90=54.0283 p99=71.7236 p99.9=87.0614 max=92.2669 mean=49.3047
lateness: n=36000 p50=0.0859 p90=0.0958 p99=0.1065 p99.9=0.1232 max=0.2948 mean=0.0861
loadgen: [{'vm': 'rv-pbf-lg-1', 'busy_max': 5.1, 'busy_mean': 4.8, 'late_max_us': 407, 'conn_opens': 228, 'max_inflight': 214}, {'vm': 'rv-pbf-lg-2', 'busy_max': 5.0, 'busy_mean': 4.6, 'late_max_us': 294, 'conn_opens': 231, 'max_inflight': 216}, {'vm': 'rv-pbf-lg-3', 'busy_max': 5.1, 'busy_mean': 4.7, 'late_max_us': 288, 'conn_opens': 227, 'max_inflight': 214}]  provider_cpu_busy_max: 19.38989553225443
gateway cores total 4.5 cpu-ms/req {'gateway': 37.627, 'workers': 33.519, 'owners': 4.094}
  rv-pbf-unit-2: cores {'launcher': 0.0, 'owner': 0.123, 'owner0': 0.061, 'owner1': 0.062, 'redis': 0.001, 'worker': 1.012} worker util max 0.112 per-core max 0.062 mean 0.05 gpu {'0': {'n': 298, 'sm_mean': 4.4, 'sm_p95': 11.0, 'sm_max': 22.0}, '1': {'n': 298, 'sm_mean': 4.9, 'sm_p95': 11.0, 'sm_max': 17.0}} t_input_p99 12.6484 per-worker admitted {'n': 18, 'min': 242, 'max': 1190, 'mean': 499.2, 'max_over_mean': 2.384, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 1, 2, 2, 2, 3, 3, 3, 3, 5, 5, 6, 21]}
  rv-pbf-unit-3: cores {'launcher': 0.0, 'owner': 0.122, 'owner0': 0.064, 'owner1': 0.057, 'redis': 0.001, 'worker': 0.996} worker util max 0.092 per-core max 0.057 mean 0.049 gpu {'0': {'n': 298, 'sm_mean': 4.6, 'sm_p95': 11.0, 'sm_max': 15.0}, '1': {'n': 298, 'sm_mean': 4.3, 'sm_p95': 11.0, 'sm_max': 18.0}} t_input_p99 12.5174 per-worker admitted {'n': 18, 'min': 251, 'max': 954, 'mean': 499.2, 'max_over_mean': 1.911, 'sheds_per_worker': [0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 3, 3, 4, 5, 5, 6, 10]}
  rv-pbf-unit-4: cores {'launcher': 0.0, 'owner': 0.125, 'owner0': 0.071, 'owner1': 0.054, 'redis': 0.001, 'worker': 1.016} worker util max 0.09 per-core max 0.061 mean 0.05 gpu {'0': {'n': 298, 'sm_mean': 5.1, 'sm_p95': 11.0, 'sm_max': 20.0}, '1': {'n': 298, 'sm_mean': 4.0, 'sm_p95': 10.0, 'sm_max': 16.0}} t_input_p99 12.6484 per-worker admitted {'n': 18, 'min': 244, 'max': 866, 'mean': 499.3, 'max_over_mean': 1.735, 'sheds_per_worker': [0, 0, 0, 1, 2, 2, 2, 2, 2, 2, 2, 3, 3, 3, 5, 5, 6, 10]}
  rv-pbf-unit-5: cores {'launcher': 0.0, 'owner': 0.12, 'owner0': 0.06, 'owner1': 0.06, 'redis': 0.001, 'worker': 0.984} worker util max 0.083 per-core max 0.057 mean 0.048 gpu {'0': {'n': 298, 'sm_mean': 4.4, 'sm_p95': 12.0, 'sm_max': 16.0}, '1': {'n': 298, 'sm_mean': 4.4, 'sm_p95': 11.0, 'sm_max': 17.0}} t_input_p99 12.3863 per-worker admitted {'n': 18, 'min': 238, 'max': 857, 'mean': 499.7, 'max_over_mean': 1.715, 'sheds_per_worker': [0, 0, 0, 0, 0, 2, 2, 2, 2, 2, 2, 3, 3, 3, 4, 4, 6, 6]}
W t_input_ns: {'n': 35763, 'mean_ms': 7.4098, 'p50_ms': 8.0937, 'p90_ms': 10.027, 'p99_ms': 12.5174, 'p99.9_ms': 16.0563, 'max_cum_ms': 36.429}
W t_tokenize_ns: {'n': 35953, 'mean_ms': 2.6774, 'p50_ms': 2.7034, 'p90_ms': 4.0796, 'p99_ms': 4.6858, 'p99.9_ms': 5.8655, 'max_cum_ms': 8.9355}
W t_guard_wait_ns: {'n': 35763, 'mean_ms': 4.2036, 'p50_ms': 4.7514, 'p90_ms': 5.2101, 'p99_ms': 7.4383, 'p99.9_ms': 11.2067, 'max_cum_ms': 30.0756}
W guard_owner_rtt_ns: {'n': 35763, 'mean_ms': 4.3734, 'p50_ms': 4.948, 'p90_ms': 5.4067, 'p99_ms': 7.6349, 'p99.9_ms': 10.6824, 'max_cum_ms': 28.7454}
W guard_queue_ns: {'n': 35763, 'mean_ms': 0.11, 'p50_ms': 0.107, 'p90_ms': 0.1285, 'p99_ms': 0.1567, 'p99.9_ms': 1.3681, 'max_cum_ms': 16.1001}
W guard_exec_ns: {'n': 35763, 'mean_ms': 3.5841, 'p50_ms': 4.2271, 'p90_ms': 4.4892, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 9.1328}
W t_admit_ns: {'n': 35953, 'mean_ms': 0.0942, 'p50_ms': 0.0886, 'p90_ms': 0.105, 'p99_ms': 0.1295, 'p99.9_ms': 1.0363, 'max_cum_ms': 21.7487}
W release_processing_ns: {'n': 5468102, 'mean_ms': 0.0632, 'p50_ms': 0.0632, 'p90_ms': 0.0783, 'p99_ms': 0.1009, 'p99.9_ms': 0.1295, 'max_cum_ms': 26.8838}
W loop_lag_ns: {'n': 214600, 'mean_ms': 0.7022, 'p50_ms': 0.0084, 'p90_ms': 1.2534, 'p99_ms': 7.0451, 'p99.9_ms': 8.5852, 'max_cum_ms': 27.862}
W audit_batch_write_ns: {'n': 70908, 'mean_ms': 1.0636, 'p50_ms': 0.9544, 'p90_ms': 1.4664, 'p99_ms': 1.794, 'p99.9_ms': 7.6349, 'max_cum_ms': 40.9418}
W counts: {"admitted": 35953, "audit_enqueued": 70924, "audit_written": 70924, "background_round_trips": 42920, "disposition_ALLOW": 35098, "disposition_BLOCK": 665, "guard_windows": 58835, "lease_refills": 176, "provider_calls": 35098, "provider_connections_opened": 4999, "requests_by_round_trips{n=\"0\"}": 35777, "requests_by_round_trips{n=\"1\"}": 176, "shared_state_round_trips": 176, "shed{reason=\"guard_queue\"}": 191}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 0.143}, "per_proc_util": {"nginx": [0.0, 0.006, 0.007, 0.007, 0.007, 0.008, 0.008, 0.009, 0.009, 0.009, 0.009, 0.01, 0.01, 0.01, 0.01, 0.012, 0.012]}, "per_core_util": {"max": 0.01, "mean": 0.009, "sum": 0.15, "n": 16}, "nic": null, "restarted": [], "loadavg_max": 0.37} nginx cpu-ms/req 1.2
redis: ops/s 828.1 ops/req 6.901 cpu cores 0.007 clients 363 mem 857.3MB ping(us) {'n': 26845, 'p50_us': 1071.1, 'p90_us': 1105.3, 'p99_us': 1224.9, 'p99.9_us': 2181.5, 'max_us': 5600.6, 'mean_us': 1082.5}
redis cmdstats: {'info': {'calls_per_s': 0.0, 'usec_per_call': 154.0}, 'ping': {'calls_per_s': 89.3, 'usec_per_call': 0.1}, 'evalsha': {'calls_per_s': 0.6, 'usec_per_call': 11.76}, 'xadd': {'calls_per_s': 236.4, 'usec_per_call': 3.58}, 'mget': {'calls_per_s': 143.0, 'usec_per_call': 0.38}, 'hgetall': {'calls_per_s': 357.6, 'usec_per_call': 0.22}, 'get': {'calls_per_s': 0.6, 'usec_per_call': 0.69}, 'decrby': {'calls_per_s': 0.6, 'usec_per_call': 0.33}}
wire: {'requests_in_window': 36000, 'unit_ip_bytes_per_req': {'unit_to_client_side': 55831.9, 'client_side_to_unit': 9719.5, 'unit_to_provider': 10153.8, 'provider_to_unit': 56511.0, 'unit_to_redis': 5786.6, 'redis_to_unit': 457.1}, 'edge_ip_bytes_per_req': {'edge_to_clients': 55955.9, 'clients_to_edge': 8182.2}, 'olg_resp_body_bytes_mean': {'sse': 67485.5, 'json': 1430.3}}
