# f1u-075-r3: strict FAIL | load-knee PASS (sut, units=1, rate=75)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 22500 (75.0/s) qualified 22143 (73.81/s) FP-blocks 349 (0.01551) infra 8 (0.00035555555555555557) drops 0 safety 0
infra reasons: {'http_503': 8, 'incomplete': 8, 'unjoined': 8, 'disposition_missing': 8, 'stage_canon_missing': 8, 'stage_det_missing': 8, 'stage_sem_missing': 8, 'stage_resolve_missing': 8, 'stage_dispatch_missing': 8, 'stage_out_missing': 8, 'stage_audit_missing': 8}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 8}

T_fw_addon: n=22143 p50=10.673 p90=15.75 p99=53.8072 p99.9=71.2809 max=75.7339 mean=12.9507
T_fw_addon_nohold: n=22143 p50=10.4007 p90=13.5307 p99=16.3909 p99.9=22.2373 max=33.1196 mean=9.9053
T_fw_addon_sse: n=15503 p50=10.7795 p90=31.6795 p99=54.5998 p99.9=71.8514 max=75.7339 mean=14.2293
T_fw_addon_json: n=6640 p50=10.4581 p90=13.6702 p99=16.5346 p99.9=23.127 max=28.132 mean=9.9655
T_addon_first_sse: n=15503 p50=10.3168 p90=13.8209 p99=28.5777 p99.9=34.2867 max=53.2329 mean=10.141
T_addon_total_sse: n=15503 p50=10.3806 p90=13.4594 p99=16.2998 p99.9=21.8212 max=33.1196 mean=9.8796
T_addon_total_json: n=6640 p50=10.4581 p90=13.6702 p99=16.5346 p99.9=23.127 max=28.132 mean=9.9655
T_release_lag_max: n=1542 p50=50.0484 p90=54.6059 p99=71.8514 p99.9=74.6106 max=75.7339 mean=49.645
lateness: n=22500 p50=0.0897 p90=0.1034 p99=0.1191 p99.9=0.1356 max=0.2226 mean=0.0885
loadgen: [{'vm': 'rv-pbf-lg-1', 'busy_max': 15.2, 'busy_mean': 4.3, 'late_max_us': 318, 'conn_opens': 151, 'max_inflight': 137}, {'vm': 'rv-pbf-lg-2', 'busy_max': 13.9, 'busy_mean': 4.3, 'late_max_us': 189, 'conn_opens': 150, 'max_inflight': 137}, {'vm': 'rv-pbf-lg-3', 'busy_max': 18.0, 'busy_mean': 4.3, 'late_max_us': 208, 'conn_opens': 150, 'max_inflight': 137}]  provider_cpu_busy_max: 20.975613260689517
gateway cores total 2.61 cpu-ms/req {'gateway': 34.897, 'workers': 30.87, 'owners': 4.021}
  rv-pbf-unit-2: cores {'launcher': 0.0, 'owner': 0.301, 'owner0': 0.145, 'owner1': 0.156, 'redis': 0.001, 'worker': 2.308} worker util max 0.17 per-core max 0.138 mean 0.112 gpu {'0': {'n': 298, 'sm_mean': 10.8, 'sm_p95': 20.0, 'sm_max': 28.0}, '1': {'n': 298, 'sm_mean': 11.6, 'sm_p95': 21.0, 'sm_max': 27.0}} t_input_p99 12.9106 per-worker admitted {'n': 18, 'min': 755, 'max': 1752, 'mean': 1248.4, 'max_over_mean': 1.403, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 2]}
W t_input_ns: {'n': 22463, 'mean_ms': 7.6482, 'p50_ms': 8.3558, 'p90_ms': 10.4202, 'p99_ms': 12.9106, 'p99.9_ms': 17.9569, 'max_cum_ms': 36.429}
W t_tokenize_ns: {'n': 22471, 'mean_ms': 2.9152, 'p50_ms': 2.9655, 'p90_ms': 4.4892, 'p99_ms': 5.079, 'p99.9_ms': 6.2587, 'max_cum_ms': 8.9355}
W t_guard_wait_ns: {'n': 22463, 'mean_ms': 4.1872, 'p50_ms': 4.7514, 'p90_ms': 5.2101, 'p99_ms': 7.3073, 'p99.9_ms': 13.1727, 'max_cum_ms': 29.7032}
W guard_owner_rtt_ns: {'n': 22463, 'mean_ms': 4.3559, 'p50_ms': 4.948, 'p90_ms': 5.4067, 'p99_ms': 7.5039, 'p99.9_ms': 11.862, 'max_cum_ms': 28.7454}
W guard_queue_ns: {'n': 22463, 'mean_ms': 0.1034, 'p50_ms': 0.1009, 'p90_ms': 0.1244, 'p99_ms': 0.1526, 'p99.9_ms': 0.1915, 'max_cum_ms': 16.1001}
W guard_exec_ns: {'n': 22463, 'mean_ms': 3.5644, 'p50_ms': 4.2271, 'p90_ms': 4.4237, 'p99_ms': 6.5208, 'p99.9_ms': 6.6519, 'max_cum_ms': 8.1548}
W t_admit_ns: {'n': 22471, 'mean_ms': 0.0911, 'p50_ms': 0.0855, 'p90_ms': 0.1039, 'p99_ms': 0.1321, 'p99.9_ms': 1.0281, 'max_cum_ms': 21.7487}
W release_processing_ns: {'n': 3437024, 'mean_ms': 0.0629, 'p50_ms': 0.0622, 'p90_ms': 0.0814, 'p99_ms': 0.105, 'p99.9_ms': 0.1341, 'max_cum_ms': 26.8838}
W loop_lag_ns: {'n': 53580, 'mean_ms': 0.8107, 'p50_ms': 0.0078, 'p90_ms': 2.8672, 'p99_ms': 8.1592, 'p99.9_ms': 10.2892, 'max_cum_ms': 27.5291}
W audit_batch_write_ns: {'n': 44591, 'mean_ms': 1.0443, 'p50_ms': 0.9789, 'p90_ms': 1.237, 'p99_ms': 1.876, 'p99.9_ms': 8.8474, 'max_cum_ms': 40.9418}
W counts: {"admitted": 22471, "audit_enqueued": 44619, "audit_written": 44619, "background_round_trips": 10716, "disposition_ALLOW": 22117, "disposition_BLOCK": 346, "guard_windows": 37081, "lease_refills": 101, "provider_calls": 22117, "provider_connections_opened": 3390, "requests_by_round_trips{n=\"0\"}": 22370, "requests_by_round_trips{n=\"1\"}": 101, "shared_state_round_trips": 101, "shed{reason=\"guard_queue\"}": 8}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 0.086}, "per_proc_util": {"nginx": [0.0, 0.004, 0.004, 0.004, 0.005, 0.005, 0.005, 0.005, 0.005, 0.005, 0.005, 0.006, 0.006, 0.007, 0.007, 0.007, 0.007]}, "per_core_util": {"max": 0.008, "mean": 0.006, "sum": 0.1, "n": 16}, "nic": null, "restarted": [], "loadavg_max": 0.15} nginx cpu-ms/req 1.147
redis: ops/s 745.7 ops/req 9.943 cpu cores 0.005 clients 291 mem 1675.1MB ping(us) {'n': 28308, 'p50_us': 486.5, 'p90_us': 517.8, 'p99_us': 633.2, 'p99.9_us': 2038.1, 'max_us': 5116.5, 'mean_us': 499.8}
redis cmdstats: {'info': {'calls_per_s': 0.0, 'usec_per_call': 147.0}, 'ping': {'calls_per_s': 94.2, 'usec_per_call': 0.11}, 'evalsha': {'calls_per_s': 0.3, 'usec_per_call': 11.53}, 'xadd': {'calls_per_s': 148.7, 'usec_per_call': 3.65}, 'mget': {'calls_per_s': 143.3, 'usec_per_call': 0.38}, 'hgetall': {'calls_per_s': 358.4, 'usec_per_call': 0.2}, 'get': {'calls_per_s': 0.3, 'usec_per_call': 0.6}, 'decrby': {'calls_per_s': 0.3, 'usec_per_call': 0.35}}
wire: {'requests_in_window': 22500, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56168.2, 'client_side_to_unit': 8281.5, 'unit_to_provider': 9123.3, 'provider_to_unit': 56850.5, 'unit_to_redis': 5611.8, 'redis_to_unit': 389.8}, 'edge_ip_bytes_per_req': {'edge_to_clients': 56293.8, 'clients_to_edge': 8459.5}, 'olg_resp_body_bytes_mean': {'sse': 67276.9, 'json': 1430.7}}
