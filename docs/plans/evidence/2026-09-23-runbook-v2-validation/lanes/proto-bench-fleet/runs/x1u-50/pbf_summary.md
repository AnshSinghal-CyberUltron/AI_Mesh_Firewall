# x1u-50: strict FAIL | load-knee PASS (sut, units=1, rate=50)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 3000 (50.0/s) qualified 2960 (49.33/s) FP-blocks 40 (0.01333) infra 0 (0.0) drops 0 safety 0
infra reasons: {}
infra detail: {}

T_fw_addon: n=2960 p50=10.6924 p90=15.8278 p99=54.5471 p99.9=71.7688 max=72.1373 mean=13.0702
T_fw_addon_nohold: n=2960 p50=10.4112 p90=13.7216 p99=16.3492 p99.9=21.0195 max=21.5855 mean=10.0071
T_fw_addon_sse: n=2066 p50=10.8343 p90=31.9366 p99=56.3523 p99.9=71.7688 max=72.1373 mean=14.3931
T_fw_addon_json: n=894 p50=10.1139 p90=13.936 p99=16.9601 p99.9=21.5855 max=21.5855 mean=10.0131
T_addon_first_sse: n=2066 p50=10.3717 p90=13.7796 p99=28.8671 p99.9=35.3335 max=36.2032 mean=10.2229
T_addon_total_sse: n=2066 p50=10.4627 p90=13.6131 p99=16.2321 p99.9=19.4056 max=21.5233 mean=10.0045
T_addon_total_json: n=894 p50=10.1139 p90=13.936 p99=16.9601 p99.9=21.5855 max=21.5855 mean=10.0131
T_release_lag_max: n=205 p50=50.4045 p90=56.3523 p99=71.7688 p99.9=72.1373 max=72.1373 mean=50.6677
lateness: n=3000 p50=0.092 p90=0.1081 p99=0.1244 p99.9=0.1689 max=0.1818 mean=0.0929
loadgen: [{'vm': 'rv-pbf-lg-1', 'busy_max': 4.3, 'busy_mean': 4.0, 'late_max_us': 221, 'conn_opens': 91, 'max_inflight': 91}, {'vm': 'rv-pbf-lg-2', 'busy_max': 4.1, 'busy_mean': 3.9, 'late_max_us': 170, 'conn_opens': 91, 'max_inflight': 91}, {'vm': 'rv-pbf-lg-3', 'busy_max': 4.1, 'busy_mean': 3.9, 'late_max_us': 181, 'conn_opens': 91, 'max_inflight': 91}]  provider_cpu_busy_max: 14.347949332821052
gateway cores total 1.82 cpu-ms/req {'gateway': 37.106, 'workers': 33.044, 'owners': 4.054}
  rv-pbf-unit-2: cores {'launcher': 0.0, 'owner': 0.199, 'owner0': 0.106, 'owner1': 0.093, 'redis': 0.001, 'worker': 1.625} worker util max 0.115 per-core max 0.103 mean 0.079 gpu {'0': {'n': 60, 'sm_mean': 7.7, 'sm_p95': 14.0, 'sm_max': 22.0}, '1': {'n': 60, 'sm_mean': 7.0, 'sm_p95': 15.0, 'sm_max': 18.0}} t_input_p99 12.6484
W t_input_ns: {'n': 2995, 'mean_ms': 7.5968, 'p50_ms': 8.2903, 'p90_ms': 10.2892, 'p99_ms': 12.6484, 'p99.9_ms': 15.532, 'max_cum_ms': 35.9043}
W t_tokenize_ns: {'n': 2993, 'mean_ms': 2.8214, 'p50_ms': 2.8344, 'p90_ms': 4.3581, 'p99_ms': 4.948, 'p99.9_ms': 6.1932, 'max_cum_ms': 8.9355}
W t_guard_wait_ns: {'n': 2995, 'mean_ms': 4.1997, 'p50_ms': 4.8169, 'p90_ms': 5.2101, 'p99_ms': 7.2417, 'p99.9_ms': 11.5999, 'max_cum_ms': 29.2545}
W guard_owner_rtt_ns: {'n': 2995, 'mean_ms': 4.3683, 'p50_ms': 5.0135, 'p90_ms': 5.4067, 'p99_ms': 7.4383, 'p99.9_ms': 10.8134, 'max_cum_ms': 27.7519}
W guard_queue_ns: {'n': 2995, 'mean_ms': 0.1121, 'p50_ms': 0.1101, 'p90_ms': 0.1285, 'p99_ms': 0.1546, 'p99.9_ms': 0.9789, 'max_cum_ms': 16.1001}
W guard_exec_ns: {'n': 2995, 'mean_ms': 3.5298, 'p50_ms': 4.2271, 'p90_ms': 4.4237, 'p99_ms': 6.4553, 'p99.9_ms': 6.5864, 'max_cum_ms': 8.1548}
W t_admit_ns: {'n': 2993, 'mean_ms': 0.1004, 'p50_ms': 0.0937, 'p90_ms': 0.1193, 'p99_ms': 0.1526, 'p99.9_ms': 1.0895, 'max_cum_ms': 21.7487}
W release_processing_ns: {'n': 472076, 'mean_ms': 0.0646, 'p50_ms': 0.0637, 'p90_ms': 0.0845, 'p99_ms': 0.1121, 'p99.9_ms': 0.1423, 'max_cum_ms': 5.7792}
W loop_lag_ns: {'n': 10760, 'mean_ms': 0.7523, 'p50_ms': 0.0188, 'p90_ms': 1.2534, 'p99_ms': 7.7005, 'p99.9_ms': 9.6338, 'max_cum_ms': 21.4172}
W audit_batch_write_ns: {'n': 5958, 'mean_ms': 1.039, 'p50_ms': 0.9789, 'p90_ms': 1.237, 'p99_ms': 1.6957, 'p99.9_ms': 7.9626, 'max_cum_ms': 40.9418}
W counts: {"admitted": 2993, "audit_enqueued": 5963, "audit_written": 5963, "background_round_trips": 2152, "disposition_ALLOW": 2955, "disposition_BLOCK": 40, "guard_windows": 4833, "lease_refills": 13, "provider_calls": 2955, "provider_connections_opened": 602, "requests_by_round_trips{n=\"0\"}": 2980, "requests_by_round_trips{n=\"1\"}": 13, "shared_state_round_trips": 13}
edge: {"window_s": 61.0, "cores_by_role": {"nginx": 0.07}, "per_proc_util": {"nginx": [0.0, 0.003, 0.003, 0.003, 0.003, 0.004, 0.004, 0.004, 0.004, 0.004, 0.005, 0.005, 0.005, 0.005, 0.006, 0.006, 0.006]}, "per_core_util": {"max": 0.007, "mean": 0.005, "sum": 0.07, "n": 16}, "nic": null, "restarted": [], "loadavg_max": 0.0} nginx cpu-ms/req 1.425
redis: ops/s 758.6 ops/req 15.173 cpu cores 0.005 clients 282 mem 306.0MB ping(us) {'n': 5695, 'p50_us': 427.9, 'p90_us': 472.6, 'p99_us': 588.4, 'p99.9_us': 1866.3, 'max_us': 6018.4, 'mean_us': 443.4}
redis cmdstats: {'info': {'calls_per_s': 0.0, 'usec_per_call': 127.0}, 'ping': {'calls_per_s': 94.1, 'usec_per_call': 0.1}, 'evalsha': {'calls_per_s': 0.2, 'usec_per_call': 11.31}, 'xadd': {'calls_per_s': 98.9, 'usec_per_call': 3.57}, 'mget': {'calls_per_s': 161.3, 'usec_per_call': 0.37}, 'hgetall': {'calls_per_s': 403.6, 'usec_per_call': 0.19}, 'get': {'calls_per_s': 0.2, 'usec_per_call': 0.69}, 'decrby': {'calls_per_s': 0.2, 'usec_per_call': 0.31}}
wire: {'requests_in_window': 3000, 'unit_ip_bytes_per_req': {'unit_to_client_side': 57547.0, 'client_side_to_unit': 8681.6, 'unit_to_provider': 9604.9, 'provider_to_unit': 58254.7, 'unit_to_redis': 5707.4, 'redis_to_unit': 458.6}, 'edge_ip_bytes_per_req': {'edge_to_clients': 57706.7, 'clients_to_edge': 8883.4}, 'olg_resp_body_bytes_mean': {'sse': 68760.1, 'json': 1415.3}}
