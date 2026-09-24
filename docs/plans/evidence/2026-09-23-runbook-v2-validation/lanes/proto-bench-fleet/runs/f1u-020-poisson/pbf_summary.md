# f1u-020-poisson: strict FAIL | load-knee FAIL (sut, units=1, rate=20)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 6154 (20.51/s) qualified 6028 (20.09/s) FP-blocks 76 (0.01235) infra 50 (0.008124796880077998) drops 0 safety 0
infra reasons: {'http_503': 50, 'incomplete': 50, 'unjoined': 50, 'disposition_missing': 50, 'stage_canon_missing': 50, 'stage_det_missing': 50, 'stage_sem_missing': 50, 'stage_resolve_missing': 50, 'stage_dispatch_missing': 50, 'stage_out_missing': 50, 'stage_audit_missing': 50}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 50}

T_fw_addon: n=6028 p50=10.284 p90=15.0761 p99=53.2779 p99.9=71.2921 max=74.8098 mean=12.6468
T_fw_addon_nohold: n=6028 p50=10.0398 p90=12.6432 p99=15.2876 p99.9=17.9689 max=21.5965 mean=9.4415
T_fw_addon_sse: n=4215 p50=10.3958 p90=35.5855 p99=53.6571 p99.9=71.514 max=74.8098 mean=14.0725
T_fw_addon_json: n=1813 p50=9.9132 p90=12.3728 p99=15.1061 p99.9=18.1876 max=21.5965 mean=9.3324
T_addon_first_sse: n=4215 p50=10.0404 p90=13.2508 p99=29.8433 p99.9=33.6324 max=34.0144 mean=9.7414
T_addon_total_sse: n=4215 p50=10.0777 p90=12.7526 p99=15.4912 p99.9=17.9689 max=21.0181 mean=9.4885
T_addon_total_json: n=1813 p50=9.9132 p90=12.3728 p99=15.1061 p99.9=18.1876 max=21.5965 mean=9.3324
T_release_lag_max: n=456 p50=49.7743 p90=53.597 p99=71.514 p99.9=74.8098 max=74.8098 mean=48.7967
lateness: n=6154 p50=0.0855 p90=0.0977 p99=0.1193 p99.9=0.1439 max=0.1567 mean=0.0863
loadgen: [{'vm': 'rv-pbf-lg-1', 'busy_max': 8.9, 'busy_mean': 3.5, 'late_max_us': 228, 'conn_opens': 56, 'max_inflight': 51}, {'vm': 'rv-pbf-lg-2', 'busy_max': 5.0, 'busy_mean': 3.4, 'late_max_us': 204, 'conn_opens': 55, 'max_inflight': 49}, {'vm': 'rv-pbf-lg-3', 'busy_max': 5.0, 'busy_mean': 3.4, 'late_max_us': 156, 'conn_opens': 60, 'max_inflight': 51}]  provider_cpu_busy_max: 22.418837530216827
gateway cores total 0.8 cpu-ms/req {'gateway': 39.329, 'workers': 35.224, 'owners': 4.085}
  rv-pbf-unit-2: cores {'launcher': 0.0, 'owner': 0.084, 'owner0': 0.044, 'owner1': 0.039, 'redis': 0.001, 'worker': 0.72} worker util max 0.06 per-core max 0.043 mean 0.036 gpu {'0': {'n': 298, 'sm_mean': 3.1, 'sm_p95': 8.0, 'sm_max': 17.0}, '1': {'n': 298, 'sm_mean': 2.7, 'sm_p95': 7.0, 'sm_max': 13.0}} t_input_p99 12.5174 per-worker admitted {'n': 18, 'min': 163, 'max': 589, 'mean': 341.9, 'max_over_mean': 1.723, 'sheds_per_worker': [0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 3, 4, 4, 4, 5, 6, 6, 10]}
W t_input_ns: {'n': 6105, 'mean_ms': 7.3298, 'p50_ms': 8.0282, 'p90_ms': 10.027, 'p99_ms': 12.5174, 'p99.9_ms': 15.7942, 'max_cum_ms': 19.5163}
W t_tokenize_ns: {'n': 6155, 'mean_ms': 2.572, 'p50_ms': 2.6051, 'p90_ms': 3.9813, 'p99_ms': 4.5548, 'p99.9_ms': 5.3412, 'max_cum_ms': 7.0281}
W t_guard_wait_ns: {'n': 6105, 'mean_ms': 4.239, 'p50_ms': 4.7514, 'p90_ms': 5.8655, 'p99_ms': 8.2903, 'p99.9_ms': 10.9445, 'max_cum_ms': 14.6191}
W guard_owner_rtt_ns: {'n': 6105, 'mean_ms': 4.3997, 'p50_ms': 4.948, 'p90_ms': 5.8655, 'p99_ms': 7.8316, 'p99.9_ms': 10.9445, 'max_cum_ms': 14.9315}
W guard_queue_ns: {'n': 6105, 'mean_ms': 0.185, 'p50_ms': 0.1091, 'p90_ms': 0.1321, 'p99_ms': 3.2604, 'p99.9_ms': 5.1446, 'max_cum_ms': 6.3415}
W guard_exec_ns: {'n': 6105, 'mean_ms': 3.5551, 'p50_ms': 4.2271, 'p90_ms': 4.4892, 'p99_ms': 6.5208, 'p99.9_ms': 6.6519, 'max_cum_ms': 7.1712}
W t_admit_ns: {'n': 6155, 'mean_ms': 0.0922, 'p50_ms': 0.0876, 'p90_ms': 0.1019, 'p99_ms': 0.1234, 'p99.9_ms': 1.0568, 'max_cum_ms': 7.0973}
W release_processing_ns: {'n': 951112, 'mean_ms': 0.0636, 'p50_ms': 0.0637, 'p90_ms': 0.0773, 'p99_ms': 0.0978, 'p99.9_ms': 0.1224, 'max_cum_ms': 0.5104}
W loop_lag_ns: {'n': 53780, 'mean_ms': 0.6093, 'p50_ms': 0.0055, 'p90_ms': 1.0895, 'p99_ms': 5.8655, 'p99.9_ms': 7.766, 'max_cum_ms': 13.075}
W audit_batch_write_ns: {'n': 12122, 'mean_ms': 1.0332, 'p50_ms': 0.9789, 'p90_ms': 1.2534, 'p99_ms': 1.45, 'p99.9_ms': 6.3242, 'max_cum_ms': 8.0434}
W counts: {"admitted": 6155, "audit_enqueued": 12125, "audit_written": 12125, "background_round_trips": 10756, "disposition_ALLOW": 6029, "disposition_BLOCK": 76, "guard_windows": 9954, "lease_refills": 25, "provider_calls": 6029, "provider_connections_opened": 687, "requests_by_round_trips{n=\"0\"}": 6130, "requests_by_round_trips{n=\"1\"}": 25, "shared_state_round_trips": 25, "shed{reason=\"guard_queue\"}": 50}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 0.023}, "per_proc_util": {"nginx": [0.0, 0.001, 0.001, 0.001, 0.001, 0.001, 0.001, 0.001, 0.001, 0.002, 0.002, 0.002, 0.002, 0.002, 0.002, 0.002, 0.003]}, "per_core_util": {"max": 0.003, "mean": 0.002, "sum": 0.03, "n": 16}, "nic": null, "restarted": [], "loadavg_max": 0.08} nginx cpu-ms/req 1.121
redis: ops/s 637.4 ops/req 31.074 cpu cores 0.003 clients 253 mem 1680.9MB ping(us) {'n': 28349, 'p50_us': 485.4, 'p90_us': 513.7, 'p99_us': 615.4, 'p99.9_us': 863.4, 'max_us': 5134.2, 'mean_us': 493.4}
redis cmdstats: {'info': {'calls_per_s': 0.0, 'usec_per_call': 147.0}, 'ping': {'calls_per_s': 94.3, 'usec_per_call': 0.1}, 'evalsha': {'calls_per_s': 0.1, 'usec_per_call': 13.36}, 'xadd': {'calls_per_s': 40.3, 'usec_per_call': 3.66}, 'mget': {'calls_per_s': 143.6, 'usec_per_call': 0.36}, 'hgetall': {'calls_per_s': 358.9, 'usec_per_call': 0.27}, 'get': {'calls_per_s': 0.1, 'usec_per_call': 0.68}, 'decrby': {'calls_per_s': 0.1, 'usec_per_call': 0.64}, 'hello': {'calls_per_s': 0.0, 'usec_per_call': 3.5}}
wire: {'requests_in_window': 6154, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56727.5, 'client_side_to_unit': 9703.8, 'unit_to_provider': 10171.0, 'provider_to_unit': 57431.5, 'unit_to_redis': 6176.9, 'redis_to_unit': 792.4}, 'edge_ip_bytes_per_req': {'edge_to_clients': 56841.1, 'clients_to_edge': 10020.9}, 'olg_resp_body_bytes_mean': {'sse': 68571.3, 'json': 1442.4}}
