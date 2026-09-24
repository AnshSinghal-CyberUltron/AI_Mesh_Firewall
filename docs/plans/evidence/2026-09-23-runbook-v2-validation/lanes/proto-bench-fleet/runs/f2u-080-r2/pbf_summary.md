# f2u-080-r2: strict FAIL | load-knee PASS (sut, units=2, rate=80)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 24000 (80.0/s) qualified 23607 (78.69/s) FP-blocks 384 (0.016) infra 9 (0.000375) drops 0 safety 0
infra reasons: {'http_503': 9, 'incomplete': 9, 'unjoined': 9, 'disposition_missing': 9, 'stage_canon_missing': 9, 'stage_det_missing': 9, 'stage_sem_missing': 9, 'stage_resolve_missing': 9, 'stage_dispatch_missing': 9, 'stage_out_missing': 9, 'stage_audit_missing': 9}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 9}

T_fw_addon: n=23607 p50=10.3906 p90=15.1239 p99=53.4766 p99.9=71.5796 max=90.6283 mean=12.6946
T_fw_addon_nohold: n=23607 p50=10.172 p90=13.0163 p99=15.847 p99.9=20.6929 max=25.705 mean=9.6303
T_fw_addon_sse: n=16520 p50=10.4567 p90=31.935 p99=54.3676 p99.9=72.2889 max=90.6283 mean=13.9802
T_fw_addon_json: n=7087 p50=10.2563 p90=13.2124 p99=15.7137 p99.9=20.3434 max=22.2685 mean=9.698
T_addon_first_sse: n=16520 p50=10.0712 p90=13.3403 p99=28.6374 p99.9=33.8917 max=50.263 mean=9.8364
T_addon_total_sse: n=16520 p50=10.1405 p90=12.93 p99=15.9198 p99.9=20.8934 max=25.705 mean=9.6012
T_addon_total_json: n=7087 p50=10.2563 p90=13.2124 p99=15.7137 p99.9=20.3434 max=22.2685 mean=9.698
T_release_lag_max: n=1671 p50=49.9753 p90=54.2992 p99=72.2889 p99.9=79.2182 max=90.6283 mean=49.4732
lateness: n=24000 p50=0.088 p90=0.0971 p99=0.1075 p99.9=0.1223 max=0.335 mean=0.0882
loadgen: [{'vm': 'rv-pbf-lg-1', 'busy_max': 15.9, 'busy_mean': 4.3, 'late_max_us': 334, 'conn_opens': 151, 'max_inflight': 146}, {'vm': 'rv-pbf-lg-2', 'busy_max': 14.7, 'busy_mean': 4.2, 'late_max_us': 169, 'conn_opens': 151, 'max_inflight': 146}, {'vm': 'rv-pbf-lg-3', 'busy_max': 17.8, 'busy_mean': 4.3, 'late_max_us': 191, 'conn_opens': 151, 'max_inflight': 146}]  provider_cpu_busy_max: 17.963469563886136
gateway cores total 2.93 cpu-ms/req {'gateway': 36.765, 'workers': 32.644, 'owners': 4.11}
  rv-pbf-unit-2: cores {'launcher': 0.0, 'owner': 0.166, 'owner0': 0.086, 'owner1': 0.08, 'redis': 0.001, 'worker': 1.333} worker util max 0.112 per-core max 0.097 mean 0.066 gpu {'0': {'n': 298, 'sm_mean': 6.5, 'sm_p95': 15.0, 'sm_max': 20.0}, '1': {'n': 298, 'sm_mean': 5.9, 'sm_p95': 13.0, 'sm_max': 22.0}} t_input_p99 12.7795 per-worker admitted {'n': 18, 'min': 379, 'max': 1067, 'mean': 665.8, 'max_over_mean': 1.603, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 2]}
  rv-pbf-unit-3: cores {'launcher': 0.0, 'owner': 0.162, 'owner0': 0.076, 'owner1': 0.086, 'redis': 0.001, 'worker': 1.27} worker util max 0.103 per-core max 0.086 mean 0.062 gpu {'0': {'n': 298, 'sm_mean': 5.8, 'sm_p95': 13.0, 'sm_max': 20.0}, '1': {'n': 298, 'sm_mean': 6.2, 'sm_p95': 13.0, 'sm_max': 22.0}} t_input_p99 12.5174 per-worker admitted {'n': 18, 'min': 405, 'max': 1035, 'mean': 666.2, 'max_over_mean': 1.554, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1]}
W t_input_ns: {'n': 23968, 'mean_ms': 7.4917, 'p50_ms': 8.1592, 'p90_ms': 10.1581, 'p99_ms': 12.6484, 'p99.9_ms': 16.4495, 'max_cum_ms': 36.429}
W t_tokenize_ns: {'n': 23976, 'mean_ms': 2.7392, 'p50_ms': 2.7689, 'p90_ms': 4.2271, 'p99_ms': 4.8169, 'p99.9_ms': 5.931, 'max_cum_ms': 8.9355}
W t_guard_wait_ns: {'n': 23968, 'mean_ms': 4.2175, 'p50_ms': 4.7514, 'p90_ms': 5.2101, 'p99_ms': 7.3073, 'p99.9_ms': 11.862, 'max_cum_ms': 29.7032}
W guard_owner_rtt_ns: {'n': 23968, 'mean_ms': 4.3905, 'p50_ms': 4.948, 'p90_ms': 5.4067, 'p99_ms': 7.5694, 'p99.9_ms': 11.2067, 'max_cum_ms': 28.7454}
W guard_queue_ns: {'n': 23968, 'mean_ms': 0.1089, 'p50_ms': 0.107, 'p90_ms': 0.1295, 'p99_ms': 0.1567, 'p99.9_ms': 0.9134, 'max_cum_ms': 16.1001}
W guard_exec_ns: {'n': 23968, 'mean_ms': 3.5985, 'p50_ms': 4.2271, 'p90_ms': 4.4892, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 8.1548}
W t_admit_ns: {'n': 23977, 'mean_ms': 0.0936, 'p50_ms': 0.0886, 'p90_ms': 0.105, 'p99_ms': 0.1295, 'p99.9_ms': 1.0281, 'max_cum_ms': 21.7487}
W release_processing_ns: {'n': 3663406, 'mean_ms': 0.0632, 'p50_ms': 0.0632, 'p90_ms': 0.0783, 'p99_ms': 0.1009, 'p99.9_ms': 0.1306, 'max_cum_ms': 26.8838}
W loop_lag_ns: {'n': 107250, 'mean_ms': 0.7377, 'p50_ms': 0.0057, 'p90_ms': 1.6957, 'p99_ms': 7.4383, 'p99.9_ms': 9.3716, 'max_cum_ms': 27.5291}
W audit_batch_write_ns: {'n': 47548, 'mean_ms': 0.9903, 'p50_ms': 0.938, 'p90_ms': 1.1715, 'p99_ms': 1.4664, 'p99.9_ms': 7.9626, 'max_cum_ms': 40.9418}
W counts: {"admitted": 23977, "audit_enqueued": 47562, "audit_written": 47562, "background_round_trips": 21450, "disposition_ALLOW": 23584, "disposition_BLOCK": 384, "guard_windows": 39559, "lease_refills": 110, "provider_calls": 23584, "provider_connections_opened": 2910, "requests_by_round_trips{n=\"0\"}": 23867, "requests_by_round_trips{n=\"1\"}": 110, "shared_state_round_trips": 110, "shed{reason=\"guard_queue\"}": 9}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 0.091}, "per_proc_util": {"nginx": [0.0, 0.004, 0.004, 0.005, 0.005, 0.005, 0.005, 0.005, 0.006, 0.006, 0.006, 0.006, 0.006, 0.006, 0.006, 0.007, 0.007]}, "per_core_util": {"max": 0.008, "mean": 0.006, "sum": 0.1, "n": 16}, "nic": null, "restarted": [], "loadavg_max": 0.16} nginx cpu-ms/req 1.135
redis: ops/s 755.0 ops/req 9.438 cpu cores 0.006 clients 291 mem 1205.7MB ping(us) {'n': 28141, 'p50_us': 553.8, 'p90_us': 586.3, 'p99_us': 693.9, 'p99.9_us': 1902.2, 'max_us': 4531.7, 'mean_us': 566.7}
redis cmdstats: {'info': {'calls_per_s': 0.0, 'usec_per_call': 142.0}, 'ping': {'calls_per_s': 93.7, 'usec_per_call': 0.1}, 'evalsha': {'calls_per_s': 0.4, 'usec_per_call': 11.85}, 'xadd': {'calls_per_s': 158.5, 'usec_per_call': 3.56}, 'mget': {'calls_per_s': 143.4, 'usec_per_call': 0.38}, 'hgetall': {'calls_per_s': 358.4, 'usec_per_call': 0.2}, 'get': {'calls_per_s': 0.4, 'usec_per_call': 0.61}, 'decrby': {'calls_per_s': 0.4, 'usec_per_call': 0.44}}
wire: {'requests_in_window': 24000, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56124.1, 'client_side_to_unit': 9031.4, 'unit_to_provider': 9757.7, 'provider_to_unit': 56806.2, 'unit_to_redis': 5743.8, 'redis_to_unit': 442.3}, 'edge_ip_bytes_per_req': {'edge_to_clients': 56243.4, 'clients_to_edge': 8379.7}, 'olg_resp_body_bytes_mean': {'sse': 67307.6, 'json': 1433.2}}
