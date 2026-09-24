# f1u-070-r2: strict FAIL | load-knee PASS (sut, units=1, rate=70)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 21000 (70.0/s) qualified 20681 (68.94/s) FP-blocks 316 (0.01505) infra 3 (0.00014285714285714287) drops 0 safety 0
infra reasons: {'http_503': 3, 'incomplete': 3, 'unjoined': 3, 'disposition_missing': 3, 'stage_canon_missing': 3, 'stage_det_missing': 3, 'stage_sem_missing': 3, 'stage_resolve_missing': 3, 'stage_dispatch_missing': 3, 'stage_out_missing': 3, 'stage_audit_missing': 3}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 3}

T_fw_addon: n=20681 p50=10.6798 p90=15.4268 p99=53.7231 p99.9=71.5202 max=89.0536 mean=12.8832
T_fw_addon_nohold: n=20681 p50=10.4248 p90=13.5261 p99=16.6108 p99.9=21.4736 max=25.3151 mean=9.9321
T_fw_addon_sse: n=14471 p50=10.7758 p90=30.8054 p99=54.7448 p99.9=71.9156 max=89.0536 mean=14.1257
T_fw_addon_json: n=6210 p50=10.4742 p90=13.6639 p99=16.4318 p99.9=20.8012 max=24.3361 mean=9.9878
T_addon_first_sse: n=14471 p50=10.3441 p90=13.7072 p99=28.7662 p99.9=34.1494 max=51.22 mean=10.1314
T_addon_total_sse: n=14471 p50=10.3987 p90=13.4603 p99=16.6952 p99.9=21.4773 max=25.3151 mean=9.9082
T_addon_total_json: n=6210 p50=10.4742 p90=13.6639 p99=16.4318 p99.9=20.8012 max=24.3361 mean=9.9878
T_release_lag_max: n=1393 p50=50.1474 p90=54.8635 p99=72.3584 p99.9=79.2918 max=89.0536 mean=49.8538
lateness: n=21000 p50=0.0862 p90=0.0957 p99=0.1075 p99.9=0.1199 max=0.2375 mean=0.0863
loadgen: [{'vm': 'rv-pbf-lg-1', 'busy_max': 17.7, 'busy_mean': 4.1, 'late_max_us': 394, 'conn_opens': 140, 'max_inflight': 128}, {'vm': 'rv-pbf-lg-2', 'busy_max': 16.1, 'busy_mean': 4.1, 'late_max_us': 198, 'conn_opens': 141, 'max_inflight': 128}, {'vm': 'rv-pbf-lg-3', 'busy_max': 17.9, 'busy_mean': 4.1, 'late_max_us': 237, 'conn_opens': 139, 'max_inflight': 128}]  provider_cpu_busy_max: 17.21180363580007
gateway cores total 2.49 cpu-ms/req {'gateway': 35.639, 'workers': 31.527, 'owners': 4.106}
  rv-pbf-unit-2: cores {'launcher': 0.0, 'owner': 0.286, 'owner0': 0.15, 'owner1': 0.137, 'redis': 0.001, 'worker': 2.2} worker util max 0.182 per-core max 0.121 mean 0.107 gpu {'0': {'n': 298, 'sm_mean': 11.0, 'sm_p95': 20.0, 'sm_max': 31.0}, '1': {'n': 298, 'sm_mean': 10.8, 'sm_p95': 19.0, 'sm_max': 28.0}} t_input_p99 13.0417 per-worker admitted {'n': 18, 'min': 618, 'max': 1839, 'mean': 1165.7, 'max_over_mean': 1.578, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1]}
W t_input_ns: {'n': 20979, 'mean_ms': 7.6901, 'p50_ms': 8.3558, 'p90_ms': 10.4202, 'p99_ms': 13.0417, 'p99.9_ms': 16.9083, 'max_cum_ms': 36.429}
W t_tokenize_ns: {'n': 20982, 'mean_ms': 2.8825, 'p50_ms': 2.9, 'p90_ms': 4.4237, 'p99_ms': 5.1446, 'p99.9_ms': 6.1276, 'max_cum_ms': 8.9355}
W t_guard_wait_ns: {'n': 20979, 'mean_ms': 4.2545, 'p50_ms': 4.8169, 'p90_ms': 5.2101, 'p99_ms': 7.3728, 'p99.9_ms': 12.2552, 'max_cum_ms': 29.2545}
W guard_owner_rtt_ns: {'n': 20979, 'mean_ms': 4.4308, 'p50_ms': 5.0135, 'p90_ms': 5.4723, 'p99_ms': 7.6349, 'p99.9_ms': 11.4688, 'max_cum_ms': 27.7519}
W guard_queue_ns: {'n': 20979, 'mean_ms': 0.1112, 'p50_ms': 0.108, 'p90_ms': 0.1321, 'p99_ms': 0.1587, 'p99.9_ms': 0.9871, 'max_cum_ms': 16.1001}
W guard_exec_ns: {'n': 20979, 'mean_ms': 3.6142, 'p50_ms': 4.2926, 'p90_ms': 4.4892, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 8.1548}
W t_admit_ns: {'n': 20982, 'mean_ms': 0.0937, 'p50_ms': 0.0876, 'p90_ms': 0.106, 'p99_ms': 0.1341, 'p99.9_ms': 1.0363, 'max_cum_ms': 21.7487}
W release_processing_ns: {'n': 3210146, 'mean_ms': 0.064, 'p50_ms': 0.0632, 'p90_ms': 0.0814, 'p99_ms': 0.105, 'p99.9_ms': 0.1321, 'max_cum_ms': 5.7792}
W loop_lag_ns: {'n': 53590, 'mean_ms': 0.7995, 'p50_ms': 0.0072, 'p90_ms': 2.7034, 'p99_ms': 8.0937, 'p99.9_ms': 10.1581, 'max_cum_ms': 27.5291}
W audit_batch_write_ns: {'n': 41623, 'mean_ms': 1.0619, 'p50_ms': 0.9871, 'p90_ms': 1.2534, 'p99_ms': 2.089, 'p99.9_ms': 8.5852, 'max_cum_ms': 40.9418}
W counts: {"admitted": 20982, "audit_enqueued": 41638, "audit_written": 41639, "background_round_trips": 10718, "disposition_ALLOW": 20663, "disposition_BLOCK": 316, "guard_windows": 34688, "lease_refills": 96, "provider_calls": 20663, "provider_connections_opened": 2935, "requests_by_round_trips{n=\"0\"}": 20886, "requests_by_round_trips{n=\"1\"}": 96, "shared_state_round_trips": 96, "shed{reason=\"guard_queue\"}": 3}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 0.079}, "per_proc_util": {"nginx": [0.0, 0.004, 0.004, 0.004, 0.004, 0.004, 0.004, 0.005, 0.005, 0.005, 0.005, 0.005, 0.005, 0.006, 0.007, 0.007, 0.007]}, "per_core_util": {"max": 0.008, "mean": 0.005, "sum": 0.09, "n": 16}, "nic": null, "restarted": [], "loadavg_max": 0.16} nginx cpu-ms/req 1.135
redis: ops/s 736.8 ops/req 10.525 cpu cores 0.005 clients 255 mem 1214.6MB ping(us) {'n': 28454, 'p50_us': 433.9, 'p90_us': 461.0, 'p99_us': 554.6, 'p99.9_us': 2794.9, 'max_us': 6061.6, 'mean_us': 444.6}
redis cmdstats: {'info': {'calls_per_s': 0.0, 'usec_per_call': 144.0}, 'ping': {'calls_per_s': 94.7, 'usec_per_call': 0.1}, 'evalsha': {'calls_per_s': 0.3, 'usec_per_call': 12.07}, 'xadd': {'calls_per_s': 138.8, 'usec_per_call': 3.54}, 'mget': {'calls_per_s': 143.5, 'usec_per_call': 0.37}, 'hgetall': {'calls_per_s': 358.8, 'usec_per_call': 0.2}, 'get': {'calls_per_s': 0.3, 'usec_per_call': 0.65}, 'decrby': {'calls_per_s': 0.3, 'usec_per_call': 0.46}}
wire: {'requests_in_window': 21000, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56212.3, 'client_side_to_unit': 8260.4, 'unit_to_provider': 9129.2, 'provider_to_unit': 56905.3, 'unit_to_redis': 5624.7, 'redis_to_unit': 400.1}, 'edge_ip_bytes_per_req': {'edge_to_clients': 56331.6, 'clients_to_edge': 8485.2}, 'olg_resp_body_bytes_mean': {'sse': 67346.2, 'json': 1433.1}}
