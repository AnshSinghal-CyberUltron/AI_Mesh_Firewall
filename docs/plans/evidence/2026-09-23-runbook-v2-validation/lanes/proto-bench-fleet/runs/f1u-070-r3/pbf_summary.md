# f1u-070-r3: strict FAIL | load-knee PASS (sut, units=1, rate=70)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 21000 (70.0/s) qualified 20690 (68.97/s) FP-blocks 307 (0.01462) infra 3 (0.00014285714285714287) drops 0 safety 0
infra reasons: {'http_503': 3, 'incomplete': 3, 'unjoined': 3, 'disposition_missing': 3, 'stage_canon_missing': 3, 'stage_det_missing': 3, 'stage_sem_missing': 3, 'stage_resolve_missing': 3, 'stage_dispatch_missing': 3, 'stage_out_missing': 3, 'stage_audit_missing': 3}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 3}

T_fw_addon: n=20690 p50=10.6892 p90=15.3276 p99=53.2523 p99.9=71.0999 max=96.9151 mean=12.8512
T_fw_addon_nohold: n=20690 p50=10.4313 p90=13.5153 p99=16.4807 p99.9=21.3915 max=25.2927 mean=9.9265
T_fw_addon_sse: n=14476 p50=10.7862 p90=30.9293 p99=54.3042 p99.9=71.6755 max=96.9151 mean=14.0867
T_fw_addon_json: n=6214 p50=10.4697 p90=13.5052 p99=16.4342 p99.9=20.5397 max=22.3736 mean=9.9732
T_addon_first_sse: n=14476 p50=10.3749 p90=13.7446 p99=27.9935 p99.9=34.4628 max=54.7891 mean=10.1287
T_addon_total_sse: n=14476 p50=10.409 p90=13.5211 p99=16.4807 p99.9=21.9473 max=25.2927 mean=9.9065
T_addon_total_json: n=6214 p50=10.4697 p90=13.5052 p99=16.4342 p99.9=20.5397 max=22.3736 mean=9.9732
T_release_lag_max: n=1398 p50=50.1562 p90=54.3285 p99=71.7678 p99.9=87.1489 max=96.9151 mean=49.5532
lateness: n=21000 p50=0.0866 p90=0.0968 p99=0.1077 p99.9=0.1207 max=0.1633 mean=0.0868
loadgen: [{'vm': 'rv-pbf-lg-1', 'busy_max': 14.8, 'busy_mean': 4.2, 'late_max_us': 450, 'conn_opens': 140, 'max_inflight': 128}, {'vm': 'rv-pbf-lg-2', 'busy_max': 17.7, 'busy_mean': 4.1, 'late_max_us': 163, 'conn_opens': 139, 'max_inflight': 128}, {'vm': 'rv-pbf-lg-3', 'busy_max': 15.3, 'busy_mean': 4.1, 'late_max_us': 201, 'conn_opens': 141, 'max_inflight': 128}]  provider_cpu_busy_max: 23.048062412422933
gateway cores total 2.49 cpu-ms/req {'gateway': 35.619, 'workers': 31.508, 'owners': 4.105}
  rv-pbf-unit-2: cores {'launcher': 0.0, 'owner': 0.286, 'owner0': 0.139, 'owner1': 0.147, 'redis': 0.001, 'worker': 2.198} worker util max 0.176 per-core max 0.122 mean 0.107 gpu {'0': {'n': 299, 'sm_mean': 10.7, 'sm_p95': 20.0, 'sm_max': 31.0}, '1': {'n': 299, 'sm_mean': 10.8, 'sm_p95': 20.0, 'sm_max': 28.0}} t_input_p99 13.0417 per-worker admitted {'n': 18, 'min': 692, 'max': 1776, 'mean': 1165.9, 'max_over_mean': 1.523, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1]}
W t_input_ns: {'n': 20984, 'mean_ms': 7.7008, 'p50_ms': 8.3558, 'p90_ms': 10.4202, 'p99_ms': 13.0417, 'p99.9_ms': 17.6947, 'max_cum_ms': 36.429}
W t_tokenize_ns: {'n': 20987, 'mean_ms': 2.8818, 'p50_ms': 2.9, 'p90_ms': 4.4237, 'p99_ms': 5.1446, 'p99.9_ms': 6.4553, 'max_cum_ms': 8.9355}
W t_guard_wait_ns: {'n': 20984, 'mean_ms': 4.2628, 'p50_ms': 4.8169, 'p90_ms': 5.2101, 'p99_ms': 7.3728, 'p99.9_ms': 12.6484, 'max_cum_ms': 29.2545}
W guard_owner_rtt_ns: {'n': 20984, 'mean_ms': 4.4349, 'p50_ms': 5.0135, 'p90_ms': 5.4723, 'p99_ms': 7.6349, 'p99.9_ms': 11.862, 'max_cum_ms': 27.7519}
W guard_queue_ns: {'n': 20984, 'mean_ms': 0.1112, 'p50_ms': 0.1091, 'p90_ms': 0.1321, 'p99_ms': 0.1587, 'p99.9_ms': 0.214, 'max_cum_ms': 16.1001}
W guard_exec_ns: {'n': 20984, 'mean_ms': 3.6128, 'p50_ms': 4.2926, 'p90_ms': 4.4892, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 8.1548}
W t_admit_ns: {'n': 20987, 'mean_ms': 0.0937, 'p50_ms': 0.0876, 'p90_ms': 0.107, 'p99_ms': 0.1321, 'p99.9_ms': 1.0568, 'max_cum_ms': 21.7487}
W release_processing_ns: {'n': 3213729, 'mean_ms': 0.0643, 'p50_ms': 0.0637, 'p90_ms': 0.0824, 'p99_ms': 0.105, 'p99.9_ms': 0.1321, 'max_cum_ms': 5.7792}
W loop_lag_ns: {'n': 53620, 'mean_ms': 0.8011, 'p50_ms': 0.0072, 'p90_ms': 2.7361, 'p99_ms': 8.0282, 'p99.9_ms': 10.4202, 'max_cum_ms': 27.5291}
W audit_batch_write_ns: {'n': 41649, 'mean_ms': 1.0498, 'p50_ms': 0.9789, 'p90_ms': 1.237, 'p99_ms': 1.9579, 'p99.9_ms': 8.4541, 'max_cum_ms': 40.9418}
W counts: {"admitted": 20987, "audit_enqueued": 41674, "audit_written": 41675, "background_round_trips": 10724, "disposition_ALLOW": 20677, "disposition_BLOCK": 307, "guard_windows": 34693, "lease_refills": 97, "provider_calls": 20677, "provider_connections_opened": 2945, "requests_by_round_trips{n=\"0\"}": 20890, "requests_by_round_trips{n=\"1\"}": 97, "shared_state_round_trips": 97, "shed{reason=\"guard_queue\"}": 3}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 0.078}, "per_proc_util": {"nginx": [0.0, 0.003, 0.003, 0.004, 0.004, 0.004, 0.004, 0.004, 0.005, 0.005, 0.005, 0.005, 0.005, 0.006, 0.006, 0.007, 0.008]}, "per_core_util": {"max": 0.008, "mean": 0.005, "sum": 0.09, "n": 16}, "nic": null, "restarted": [], "loadavg_max": 0.1} nginx cpu-ms/req 1.117
redis: ops/s 736.3 ops/req 10.518 cpu cores 0.005 clients 255 mem 1357.6MB ping(us) {'n': 28308, 'p50_us': 488.6, 'p90_us': 518.1, 'p99_us': 629.4, 'p99.9_us': 1869.7, 'max_us': 4720.0, 'mean_us': 499.9}
redis cmdstats: {'info': {'calls_per_s': 0.0, 'usec_per_call': 141.0}, 'ping': {'calls_per_s': 94.2, 'usec_per_call': 0.1}, 'evalsha': {'calls_per_s': 0.3, 'usec_per_call': 13.38}, 'xadd': {'calls_per_s': 138.8, 'usec_per_call': 3.66}, 'mget': {'calls_per_s': 143.5, 'usec_per_call': 0.38}, 'hgetall': {'calls_per_s': 358.8, 'usec_per_call': 0.2}, 'get': {'calls_per_s': 0.3, 'usec_per_call': 0.78}, 'decrby': {'calls_per_s': 0.3, 'usec_per_call': 0.41}}
wire: {'requests_in_window': 21000, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56230.9, 'client_side_to_unit': 8319.8, 'unit_to_provider': 9079.4, 'provider_to_unit': 56923.6, 'unit_to_redis': 5626.4, 'redis_to_unit': 399.7}, 'edge_ip_bytes_per_req': {'edge_to_clients': 56349.7, 'clients_to_edge': 8499.9}, 'olg_resp_body_bytes_mean': {'sse': 67342.6, 'json': 1433.2}}
