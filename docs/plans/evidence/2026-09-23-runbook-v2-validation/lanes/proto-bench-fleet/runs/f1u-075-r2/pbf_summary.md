# f1u-075-r2: strict FAIL | load-knee PASS (sut, units=1, rate=75)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 22500 (75.0/s) qualified 22124 (73.75/s) FP-blocks 373 (0.01658) infra 3 (0.00013333333333333334) drops 0 safety 0
infra reasons: {'http_503': 3, 'incomplete': 3, 'unjoined': 3, 'disposition_missing': 3, 'stage_canon_missing': 3, 'stage_det_missing': 3, 'stage_sem_missing': 3, 'stage_resolve_missing': 3, 'stage_dispatch_missing': 3, 'stage_out_missing': 3, 'stage_audit_missing': 3}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 3}

T_fw_addon: n=22124 p50=10.6687 p90=15.5677 p99=53.2952 p99.9=71.075 max=87.3796 mean=12.7975
T_fw_addon_nohold: n=22124 p50=10.4061 p90=13.5201 p99=16.5371 p99.9=21.2155 max=29.7823 mean=9.9007
T_fw_addon_sse: n=15482 p50=10.7434 p90=30.2583 p99=54.5159 p99.9=71.7892 max=87.3796 mean=14.0105
T_fw_addon_json: n=6642 p50=10.4718 p90=13.6652 p99=16.692 p99.9=21.3464 max=22.6408 mean=9.9699
T_addon_first_sse: n=15482 p50=10.3134 p90=13.7666 p99=29.7139 p99.9=33.9476 max=52.1799 mean=10.1145
T_addon_total_sse: n=15482 p50=10.3859 p90=13.4295 p99=16.5072 p99.9=21.2155 max=29.7823 mean=9.871
T_addon_total_json: n=6642 p50=10.4718 p90=13.6652 p99=16.692 p99.9=21.3464 max=22.6408 mean=9.9699
T_release_lag_max: n=1452 p50=50.0316 p90=54.8058 p99=71.8716 p99.9=78.5375 max=87.3796 mean=49.6356
lateness: n=22500 p50=0.0912 p90=0.104 p99=0.1205 p99.9=0.1369 max=0.2515 mean=0.0911
loadgen: [{'vm': 'rv-pbf-lg-1', 'busy_max': 13.6, 'busy_mean': 4.2, 'late_max_us': 373, 'conn_opens': 149, 'max_inflight': 137}, {'vm': 'rv-pbf-lg-2', 'busy_max': 13.7, 'busy_mean': 4.2, 'late_max_us': 201, 'conn_opens': 150, 'max_inflight': 137}, {'vm': 'rv-pbf-lg-3', 'busy_max': 16.7, 'busy_mean': 4.3, 'late_max_us': 251, 'conn_opens': 150, 'max_inflight': 137}]  provider_cpu_busy_max: 20.733684465817458
gateway cores total 2.6 cpu-ms/req {'gateway': 34.73, 'workers': 30.706, 'owners': 4.018}
  rv-pbf-unit-2: cores {'launcher': 0.0, 'owner': 0.3, 'owner0': 0.165, 'owner1': 0.136, 'redis': 0.001, 'worker': 2.295} worker util max 0.184 per-core max 0.13 mean 0.112 gpu {'0': {'n': 298, 'sm_mean': 12.3, 'sm_p95': 20.0, 'sm_max': 28.0}, '1': {'n': 298, 'sm_mean': 10.4, 'sm_p95': 18.0, 'sm_max': 24.0}} t_input_p99 12.9106 per-worker admitted {'n': 18, 'min': 686, 'max': 1906, 'mean': 1249.2, 'max_over_mean': 1.526, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1]}
W t_input_ns: {'n': 22483, 'mean_ms': 7.6311, 'p50_ms': 8.3558, 'p90_ms': 10.4202, 'p99_ms': 12.9106, 'p99.9_ms': 17.1704, 'max_cum_ms': 36.429}
W t_tokenize_ns: {'n': 22486, 'mean_ms': 2.9154, 'p50_ms': 2.9655, 'p90_ms': 4.4892, 'p99_ms': 5.1446, 'p99.9_ms': 6.3242, 'max_cum_ms': 8.9355}
W t_guard_wait_ns: {'n': 22483, 'mean_ms': 4.1766, 'p50_ms': 4.7514, 'p90_ms': 5.1446, 'p99_ms': 7.2417, 'p99.9_ms': 12.1242, 'max_cum_ms': 29.7032}
W guard_owner_rtt_ns: {'n': 22483, 'mean_ms': 4.3467, 'p50_ms': 4.948, 'p90_ms': 5.3412, 'p99_ms': 7.5039, 'p99.9_ms': 11.5999, 'max_cum_ms': 28.7454}
W guard_queue_ns: {'n': 22483, 'mean_ms': 0.1034, 'p50_ms': 0.1009, 'p90_ms': 0.1244, 'p99_ms': 0.1505, 'p99.9_ms': 0.1894, 'max_cum_ms': 16.1001}
W guard_exec_ns: {'n': 22483, 'mean_ms': 3.5619, 'p50_ms': 4.2271, 'p90_ms': 4.4237, 'p99_ms': 6.5208, 'p99.9_ms': 6.6519, 'max_cum_ms': 8.1548}
W t_admit_ns: {'n': 22486, 'mean_ms': 0.0905, 'p50_ms': 0.0845, 'p90_ms': 0.1029, 'p99_ms': 0.1306, 'p99.9_ms': 1.0732, 'max_cum_ms': 21.7487}
W release_processing_ns: {'n': 3433900, 'mean_ms': 0.0628, 'p50_ms': 0.0622, 'p90_ms': 0.0804, 'p99_ms': 0.1039, 'p99.9_ms': 0.1321, 'max_cum_ms': 26.8838}
W loop_lag_ns: {'n': 53590, 'mean_ms': 0.81, 'p50_ms': 0.0088, 'p90_ms': 2.9, 'p99_ms': 8.1592, 'p99.9_ms': 10.027, 'max_cum_ms': 27.5291}
W audit_batch_write_ns: {'n': 44612, 'mean_ms': 1.0512, 'p50_ms': 0.9789, 'p90_ms': 1.237, 'p99_ms': 2.1135, 'p99.9_ms': 8.5852, 'max_cum_ms': 40.9418}
W counts: {"admitted": 22486, "audit_enqueued": 44632, "audit_written": 44632, "background_round_trips": 10718, "disposition_ALLOW": 22111, "disposition_BLOCK": 372, "guard_windows": 37112, "lease_refills": 105, "provider_calls": 22111, "provider_connections_opened": 3470, "requests_by_round_trips{n=\"0\"}": 22381, "requests_by_round_trips{n=\"1\"}": 105, "shared_state_round_trips": 105, "shed{reason=\"guard_queue\"}": 3}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 0.085}, "per_proc_util": {"nginx": [0.0, 0.004, 0.004, 0.004, 0.004, 0.004, 0.004, 0.005, 0.005, 0.006, 0.006, 0.006, 0.007, 0.007, 0.007, 0.007, 0.008]}, "per_core_util": {"max": 0.008, "mean": 0.006, "sum": 0.1, "n": 16}, "nic": null, "restarted": [], "loadavg_max": 0.33} nginx cpu-ms/req 1.143
redis: ops/s 745.7 ops/req 9.943 cpu cores 0.005 clients 291 mem 1522.1MB ping(us) {'n': 28292, 'p50_us': 490.3, 'p90_us': 552.1, 'p99_us': 643.9, 'p99.9_us': 2196.5, 'max_us': 3753.8, 'mean_us': 506.9}
redis cmdstats: {'info': {'calls_per_s': 0.0, 'usec_per_call': 142.0}, 'ping': {'calls_per_s': 94.2, 'usec_per_call': 0.1}, 'evalsha': {'calls_per_s': 0.3, 'usec_per_call': 11.84}, 'xadd': {'calls_per_s': 148.7, 'usec_per_call': 3.67}, 'mget': {'calls_per_s': 143.4, 'usec_per_call': 0.38}, 'hgetall': {'calls_per_s': 358.4, 'usec_per_call': 0.2}, 'get': {'calls_per_s': 0.3, 'usec_per_call': 0.69}, 'decrby': {'calls_per_s': 0.3, 'usec_per_call': 0.35}}
wire: {'requests_in_window': 22500, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56104.4, 'client_side_to_unit': 8296.4, 'unit_to_provider': 9107.5, 'provider_to_unit': 56793.8, 'unit_to_redis': 5609.2, 'redis_to_unit': 389.7}, 'edge_ip_bytes_per_req': {'edge_to_clients': 56228.5, 'clients_to_edge': 8438.7}, 'olg_resp_body_bytes_mean': {'sse': 67289.0, 'json': 1430.6}}
