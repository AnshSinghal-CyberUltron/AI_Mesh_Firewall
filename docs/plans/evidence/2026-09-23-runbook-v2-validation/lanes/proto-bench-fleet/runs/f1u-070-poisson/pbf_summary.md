# f1u-070-poisson: strict FAIL | load-knee FAIL (sut, units=1, rate=70)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 21090 (70.3/s) qualified 20091 (66.97/s) FP-blocks 321 (0.01522) infra 678 (0.032147937411095305) drops 0 safety 0
infra reasons: {'http_503': 678, 'incomplete': 678, 'unjoined': 678, 'disposition_missing': 678, 'stage_canon_missing': 678, 'stage_det_missing': 678, 'stage_sem_missing': 678, 'stage_resolve_missing': 678, 'stage_dispatch_missing': 678, 'stage_out_missing': 678, 'stage_audit_missing': 678}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 678}

T_fw_addon: n=20091 p50=10.8777 p90=16.6941 p99=54.6043 p99.9=71.7826 max=93.8147 mean=13.3426
T_fw_addon_nohold: n=20091 p50=10.6247 p90=14.0955 p99=18.0215 p99.9=21.4148 max=27.7232 mean=10.2615
T_fw_addon_sse: n=14062 p50=10.9792 p90=32.8242 p99=55.8849 p99.9=72.1343 max=93.8147 mean=14.6455
T_fw_addon_json: n=6029 p50=10.6748 p90=14.1947 p99=18.0452 p99.9=22.1839 max=27.7232 mean=10.3036
T_addon_first_sse: n=14062 p50=10.5285 p90=14.1999 p99=29.8059 p99.9=34.8753 max=55.5694 mean=10.4602
T_addon_total_sse: n=14062 p50=10.6084 p90=14.06 p99=18.0147 p99.9=21.083 max=27.0065 mean=10.2434
T_addon_total_json: n=6029 p50=10.6748 p90=14.1947 p99=18.0452 p99.9=22.1839 max=27.7232 mean=10.3036
T_release_lag_max: n=1414 p50=50.5242 p90=55.8551 p99=72.1343 p99.9=87.0636 max=93.8147 mean=50.4463
lateness: n=21090 p50=0.0849 p90=0.0947 p99=0.1051 p99.9=0.1179 max=0.1868 mean=0.0848
loadgen: [{'vm': 'rv-pbf-lg-1', 'busy_max': 4.6, 'busy_mean': 4.1, 'late_max_us': 216, 'conn_opens': 137, 'max_inflight': 132}, {'vm': 'rv-pbf-lg-2', 'busy_max': 4.6, 'busy_mean': 4.1, 'late_max_us': 163, 'conn_opens': 156, 'max_inflight': 144}, {'vm': 'rv-pbf-lg-3', 'busy_max': 4.6, 'busy_mean': 4.1, 'late_max_us': 184, 'conn_opens': 175, 'max_inflight': 146}]  provider_cpu_busy_max: 20.904195282804714
gateway cores total 2.43 cpu-ms/req {'gateway': 34.687, 'workers': 30.714, 'owners': 3.967}
  rv-pbf-unit-2: cores {'launcher': 0.0, 'owner': 0.278, 'owner0': 0.132, 'owner1': 0.146, 'redis': 0.001, 'worker': 2.152} worker util max 0.195 per-core max 0.116 mean 0.105 gpu {'0': {'n': 298, 'sm_mean': 9.4, 'sm_p95': 18.0, 'sm_max': 28.0}, '1': {'n': 298, 'sm_mean': 11.3, 'sm_p95': 23.0, 'sm_max': 41.0}} t_input_p99 15.0077 per-worker admitted {'n': 18, 'min': 722, 'max': 2194, 'mean': 1170.2, 'max_over_mean': 1.875, 'sheds_per_worker': [15, 16, 17, 18, 22, 24, 27, 29, 33, 34, 35, 35, 37, 40, 45, 58, 62, 129]}
W t_input_ns: {'n': 20387, 'mean_ms': 8.0209, 'p50_ms': 8.5852, 'p90_ms': 11.862, 'p99_ms': 15.0077, 'p99.9_ms': 17.9569, 'max_cum_ms': 36.429}
W t_tokenize_ns: {'n': 21064, 'mean_ms': 2.8974, 'p50_ms': 2.9, 'p90_ms': 4.4892, 'p99_ms': 5.4067, 'p99.9_ms': 6.5208, 'max_cum_ms': 8.9355}
W t_guard_wait_ns: {'n': 20388, 'mean_ms': 4.5508, 'p50_ms': 4.8824, 'p90_ms': 7.0451, 'p99_ms': 10.1581, 'p99.9_ms': 13.4349, 'max_cum_ms': 29.2545}
W guard_owner_rtt_ns: {'n': 20388, 'mean_ms': 4.7253, 'p50_ms': 5.079, 'p90_ms': 7.2417, 'p99_ms': 10.1581, 'p99.9_ms': 12.9106, 'max_cum_ms': 27.7519}
W guard_queue_ns: {'n': 20387, 'mean_ms': 0.3647, 'p50_ms': 0.1121, 'p90_ms': 0.5284, 'p99_ms': 4.4892, 'p99.9_ms': 7.5039, 'max_cum_ms': 16.1001}
W guard_exec_ns: {'n': 20387, 'mean_ms': 3.6166, 'p50_ms': 4.2271, 'p90_ms': 4.5548, 'p99_ms': 6.5864, 'p99.9_ms': 6.783, 'max_cum_ms': 8.1548}
W t_admit_ns: {'n': 21064, 'mean_ms': 0.094, 'p50_ms': 0.0876, 'p90_ms': 0.107, 'p99_ms': 0.1362, 'p99.9_ms': 1.0732, 'max_cum_ms': 21.7487}
W release_processing_ns: {'n': 3123462, 'mean_ms': 0.0642, 'p50_ms': 0.0637, 'p90_ms': 0.0814, 'p99_ms': 0.105, 'p99.9_ms': 0.1321, 'max_cum_ms': 5.7792}
W loop_lag_ns: {'n': 53590, 'mean_ms': 0.7981, 'p50_ms': 0.0071, 'p90_ms': 2.8017, 'p99_ms': 8.0937, 'p99.9_ms': 10.1581, 'max_cum_ms': 27.5291}
W audit_batch_write_ns: {'n': 40448, 'mean_ms': 1.0854, 'p50_ms': 0.9953, 'p90_ms': 1.2698, 'p99_ms': 3.8502, 'p99.9_ms': 8.9784, 'max_cum_ms': 40.9418}
W counts: {"admitted": 21064, "audit_enqueued": 40472, "audit_written": 40470, "background_round_trips": 10718, "disposition_ALLOW": 20066, "disposition_BLOCK": 321, "guard_owner_sheds": 1, "guard_windows": 33694, "lease_refills": 99, "provider_calls": 20066, "provider_connections_opened": 2803, "requests_by_round_trips{n=\"0\"}": 20965, "requests_by_round_trips{n=\"1\"}": 99, "shared_state_round_trips": 99, "shed{reason=\"guard_owner_queue\"}": 1, "shed{reason=\"guard_queue\"}": 675}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 0.076}, "per_proc_util": {"nginx": [0.0, 0.003, 0.004, 0.004, 0.004, 0.005, 0.005, 0.005, 0.005, 0.005, 0.005, 0.005, 0.005, 0.005, 0.005, 0.006, 0.006]}, "per_core_util": {"max": 0.005, "mean": 0.005, "sum": 0.08, "n": 16}, "nic": null, "restarted": [], "loadavg_max": 0.16} nginx cpu-ms/req 1.085
redis: ops/s 732.4 ops/req 10.418 cpu cores 0.005 clients 255 mem 1496.7MB ping(us) {'n': 28319, 'p50_us': 483.4, 'p90_us': 513.8, 'p99_us': 618.8, 'p99.9_us': 2511.6, 'max_us': 5789.7, 'mean_us': 495.6}
redis cmdstats: {'info': {'calls_per_s': 0.0, 'usec_per_call': 154.0}, 'ping': {'calls_per_s': 94.2, 'usec_per_call': 0.1}, 'evalsha': {'calls_per_s': 0.3, 'usec_per_call': 11.87}, 'xadd': {'calls_per_s': 134.9, 'usec_per_call': 3.78}, 'mget': {'calls_per_s': 143.5, 'usec_per_call': 0.37}, 'hgetall': {'calls_per_s': 358.8, 'usec_per_call': 0.2}, 'get': {'calls_per_s': 0.3, 'usec_per_call': 0.62}, 'decrby': {'calls_per_s': 0.3, 'usec_per_call': 0.36}}
wire: {'requests_in_window': 21090, 'unit_ip_bytes_per_req': {'unit_to_client_side': 54473.3, 'client_side_to_unit': 8494.3, 'unit_to_provider': 9146.1, 'provider_to_unit': 55132.4, 'unit_to_redis': 5451.7, 'redis_to_unit': 390.9}, 'edge_ip_bytes_per_req': {'edge_to_clients': 54593.9, 'clients_to_edge': 8821.2}, 'olg_resp_body_bytes_mean': {'sse': 67446.3, 'json': 1433.4}}
