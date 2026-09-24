# f1u-075: strict FAIL | load-knee PASS (sut, units=1, rate=75)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 22500 (75.0/s) qualified 22127 (73.76/s) FP-blocks 366 (0.01627) infra 7 (0.0003111111111111111) drops 0 safety 0
infra reasons: {'http_503': 6, 'incomplete': 6, 'unjoined': 6, 'disposition_missing': 6, 'stage_canon_missing': 6, 'stage_det_missing': 6, 'stage_sem_missing': 6, 'stage_resolve_missing': 6, 'stage_dispatch_missing': 6, 'stage_out_missing': 6, 'stage_audit_missing': 6, 'block_on_unavailable_sem': 1}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 6, '403 http_403 type=policy_violation code=blocked_by_policy': 1}

T_fw_addon: n=22127 p50=10.834 p90=15.8623 p99=54.0169 p99.9=71.5691 max=92.018 mean=13.1102
T_fw_addon_nohold: n=22127 p50=10.5626 p90=13.6797 p99=16.5429 p99.9=21.3573 max=29.5402 mean=10.0574
T_fw_addon_sse: n=15484 p50=10.9342 p90=31.6431 p99=54.8906 p99.9=71.8905 max=92.018 mean=14.3891
T_fw_addon_json: n=6643 p50=10.6241 p90=13.8713 p99=16.6437 p99.9=20.9579 max=24.6549 mean=10.1294
T_addon_first_sse: n=15484 p50=10.4669 p90=13.9395 p99=30.2531 p99.9=34.3441 max=55.6473 mean=10.2885
T_addon_total_sse: n=15484 p50=10.5326 p90=13.5696 p99=16.5219 p99.9=21.4322 max=29.5402 mean=10.0265
T_addon_total_json: n=6643 p50=10.6241 p90=13.8713 p99=16.6437 p99.9=20.9579 max=24.6549 mean=10.1294
T_release_lag_max: n=1538 p50=50.2582 p90=54.8906 p99=71.8905 p99.9=90.7516 max=92.018 mean=49.9025
lateness: n=22500 p50=0.0914 p90=0.1049 p99=0.1204 p99.9=0.1387 max=0.2136 mean=0.0913
loadgen: [{'vm': 'rv-pbf-lg-1', 'busy_max': 4.5, 'busy_mean': 4.2, 'late_max_us': 335, 'conn_opens': 150, 'max_inflight': 137}, {'vm': 'rv-pbf-lg-2', 'busy_max': 4.4, 'busy_mean': 4.1, 'late_max_us': 213, 'conn_opens': 151, 'max_inflight': 137}, {'vm': 'rv-pbf-lg-3', 'busy_max': 4.7, 'busy_mean': 4.2, 'late_max_us': 257, 'conn_opens': 150, 'max_inflight': 137}]  provider_cpu_busy_max: 15.510192766547892
gateway cores total 2.66 cpu-ms/req {'gateway': 35.548, 'workers': 31.454, 'owners': 4.088}
  rv-pbf-unit-2: cores {'launcher': 0.0, 'owner': 0.306, 'owner0': 0.149, 'owner1': 0.157, 'redis': 0.001, 'worker': 2.351} worker util max 0.156 per-core max 0.145 mean 0.114 gpu {'0': {'n': 298, 'sm_mean': 11.3, 'sm_p95': 20.0, 'sm_max': 31.0}, '1': {'n': 298, 'sm_mean': 11.2, 'sm_p95': 20.0, 'sm_max': 28.0}} t_input_p99 13.0417 per-worker admitted {'n': 18, 'min': 926, 'max': 1534, 'mean': 1248.5, 'max_over_mean': 1.229, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1]}
W t_input_ns: {'n': 22466, 'mean_ms': 7.7668, 'p50_ms': 8.4541, 'p90_ms': 10.5513, 'p99_ms': 13.0417, 'p99.9_ms': 17.6947, 'max_cum_ms': 36.429}
W t_tokenize_ns: {'n': 22473, 'mean_ms': 2.9535, 'p50_ms': 2.9983, 'p90_ms': 4.5548, 'p99_ms': 5.1446, 'p99.9_ms': 6.0621, 'max_cum_ms': 8.9355}
W t_guard_wait_ns: {'n': 22466, 'mean_ms': 4.2526, 'p50_ms': 4.8169, 'p90_ms': 5.2756, 'p99_ms': 7.3728, 'p99.9_ms': 12.1242, 'max_cum_ms': 29.2545}
W guard_owner_rtt_ns: {'n': 22466, 'mean_ms': 4.4271, 'p50_ms': 5.0135, 'p90_ms': 5.4723, 'p99_ms': 7.5694, 'p99.9_ms': 11.3377, 'max_cum_ms': 27.9577}
W guard_queue_ns: {'n': 22465, 'mean_ms': 0.1103, 'p50_ms': 0.108, 'p90_ms': 0.1295, 'p99_ms': 0.1587, 'p99.9_ms': 0.4198, 'max_cum_ms': 16.1001}
W guard_exec_ns: {'n': 22465, 'mean_ms': 3.6041, 'p50_ms': 4.2271, 'p90_ms': 4.4892, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 8.1548}
W t_admit_ns: {'n': 22473, 'mean_ms': 0.0942, 'p50_ms': 0.0876, 'p90_ms': 0.107, 'p99_ms': 0.1341, 'p99.9_ms': 1.0732, 'max_cum_ms': 21.7487}
W release_processing_ns: {'n': 3433397, 'mean_ms': 0.0644, 'p50_ms': 0.0637, 'p90_ms': 0.0824, 'p99_ms': 0.107, 'p99.9_ms': 0.1362, 'max_cum_ms': 5.7792}
W loop_lag_ns: {'n': 53580, 'mean_ms': 0.8056, 'p50_ms': 0.0083, 'p90_ms': 2.9983, 'p99_ms': 8.0937, 'p99.9_ms': 10.1581, 'max_cum_ms': 27.5291}
W audit_batch_write_ns: {'n': 44581, 'mean_ms': 1.0379, 'p50_ms': 0.9789, 'p90_ms': 1.2206, 'p99_ms': 1.6302, 'p99.9_ms': 9.2406, 'max_cum_ms': 40.9418}
W counts: {"admitted": 22473, "audit_enqueued": 44607, "audit_written": 44607, "background_round_trips": 10716, "disposition_ALLOW": 22103, "disposition_BLOCK": 363, "guard_deadline_expired": 1, "guard_unavailable_findings": 1, "guard_windows": 37075, "lease_refills": 107, "provider_calls": 22103, "provider_connections_opened": 3472, "requests_by_round_trips{n=\"0\"}": 22366, "requests_by_round_trips{n=\"1\"}": 107, "shared_state_round_trips": 107, "shed{reason=\"guard_queue\"}": 6}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 0.085}, "per_proc_util": {"nginx": [0.0, 0.004, 0.004, 0.004, 0.004, 0.005, 0.005, 0.005, 0.005, 0.005, 0.005, 0.006, 0.006, 0.006, 0.006, 0.007, 0.007]}, "per_core_util": {"max": 0.006, "mean": 0.006, "sum": 0.09, "n": 16}, "nic": null, "restarted": [], "loadavg_max": 0.22} nginx cpu-ms/req 1.143
redis: ops/s 746.1 ops/req 9.948 cpu cores 0.005 clients 255 mem 1649.7MB ping(us) {'n': 28253, 'p50_us': 502.8, 'p90_us': 555.2, 'p99_us': 654.4, 'p99.9_us': 2413.3, 'max_us': 5978.6, 'mean_us': 518.5}
redis cmdstats: {'info': {'calls_per_s': 0.0, 'usec_per_call': 145.0}, 'ping': {'calls_per_s': 94.0, 'usec_per_call': 0.1}, 'evalsha': {'calls_per_s': 0.4, 'usec_per_call': 11.73}, 'xadd': {'calls_per_s': 148.7, 'usec_per_call': 3.65}, 'mget': {'calls_per_s': 143.5, 'usec_per_call': 0.37}, 'hgetall': {'calls_per_s': 358.8, 'usec_per_call': 0.2}, 'get': {'calls_per_s': 0.4, 'usec_per_call': 0.6}, 'decrby': {'calls_per_s': 0.4, 'usec_per_call': 0.39}}
wire: {'requests_in_window': 22500, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56106.7, 'client_side_to_unit': 8276.7, 'unit_to_provider': 9145.5, 'provider_to_unit': 56791.8, 'unit_to_redis': 5610.0, 'redis_to_unit': 389.6}, 'edge_ip_bytes_per_req': {'edge_to_clients': 56231.9, 'clients_to_edge': 8461.6}, 'olg_resp_body_bytes_mean': {'sse': 67283.3, 'json': 1430.6}}
