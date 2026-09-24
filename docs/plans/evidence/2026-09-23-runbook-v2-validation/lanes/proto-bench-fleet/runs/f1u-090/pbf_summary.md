# f1u-090: strict FAIL | load-knee FAIL (sut, units=1, rate=90)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 27000 (90.0/s) qualified 26427 (88.09/s) FP-blocks 467 (0.0173) infra 106 (0.003925925925925926) drops 0 safety 0
infra reasons: {'http_503': 106, 'incomplete': 106, 'unjoined': 106, 'disposition_missing': 106, 'stage_canon_missing': 106, 'stage_det_missing': 106, 'stage_sem_missing': 106, 'stage_resolve_missing': 106, 'stage_dispatch_missing': 106, 'stage_out_missing': 106, 'stage_audit_missing': 106}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 106}

T_fw_addon: n=26427 p50=10.8048 p90=16.2174 p99=53.7689 p99.9=71.2136 max=91.134 mean=13.1072
T_fw_addon_nohold: n=26427 p50=10.5048 p90=13.7536 p99=17.9162 p99.9=21.7284 max=26.5045 mean=10.0559
T_fw_addon_sse: n=18489 p50=10.9077 p90=31.8265 p99=54.8579 p99.9=71.6203 max=91.134 mean=14.3831
T_fw_addon_json: n=7938 p50=10.5843 p90=13.8581 p99=17.8957 p99.9=21.8411 max=24.0492 mean=10.1356
T_addon_first_sse: n=18489 p50=10.4537 p90=13.892 p99=29.7401 p99.9=34.2969 max=51.7605 mean=10.267
T_addon_total_sse: n=18489 p50=10.4722 p90=13.6958 p99=17.9253 p99.9=21.7156 max=26.5045 mean=10.0217
T_addon_total_json: n=7938 p50=10.5843 p90=13.8581 p99=17.8957 p99.9=21.8411 max=24.0492 mean=10.1356
T_release_lag_max: n=1849 p50=50.2352 p90=54.8579 p99=71.6203 p99.9=90.9588 max=91.134 mean=49.6739
lateness: n=27000 p50=0.0862 p90=0.0954 p99=0.1056 p99.9=0.1247 max=0.3385 mean=0.0859
loadgen: [{'vm': 'rv-pbf-lg-1', 'busy_max': 17.0, 'busy_mean': 4.5, 'late_max_us': 338, 'conn_opens': 167, 'max_inflight': 163}, {'vm': 'rv-pbf-lg-2', 'busy_max': 14.7, 'busy_mean': 4.4, 'late_max_us': 236, 'conn_opens': 167, 'max_inflight': 164}, {'vm': 'rv-pbf-lg-3', 'busy_max': 16.8, 'busy_mean': 4.4, 'late_max_us': 220, 'conn_opens': 169, 'max_inflight': 164}]  provider_cpu_busy_max: 20.38688043589898
gateway cores total 3.14 cpu-ms/req {'gateway': 35.052, 'workers': 30.995, 'owners': 4.052}
  rv-pbf-unit-2: cores {'launcher': 0.0, 'owner': 0.363, 'owner0': 0.173, 'owner1': 0.19, 'redis': 0.001, 'worker': 2.78} worker util max 0.208 per-core max 0.153 mean 0.135 gpu {'0': {'n': 298, 'sm_mean': 13.2, 'sm_p95': 22.0, 'sm_max': 34.0}, '1': {'n': 298, 'sm_mean': 14.0, 'sm_p95': 24.0, 'sm_max': 29.0}} t_input_p99 13.697 per-worker admitted {'n': 18, 'min': 1067, 'max': 2155, 'mean': 1499.2, 'max_over_mean': 1.437, 'sheds_per_worker': [2, 3, 3, 3, 4, 4, 4, 5, 6, 6, 6, 7, 7, 7, 7, 8, 8, 15]}
W t_input_ns: {'n': 26883, 'mean_ms': 7.7635, 'p50_ms': 8.4541, 'p90_ms': 10.6824, 'p99_ms': 13.697, 'p99.9_ms': 18.219, 'max_cum_ms': 36.429}
W t_tokenize_ns: {'n': 26985, 'mean_ms': 2.9389, 'p50_ms': 2.9655, 'p90_ms': 4.5548, 'p99_ms': 5.3412, 'p99.9_ms': 6.4553, 'max_cum_ms': 8.9355}
W t_guard_wait_ns: {'n': 26883, 'mean_ms': 4.2615, 'p50_ms': 4.8169, 'p90_ms': 5.2756, 'p99_ms': 8.9784, 'p99.9_ms': 12.9106, 'max_cum_ms': 29.2545}
W guard_owner_rtt_ns: {'n': 26883, 'mean_ms': 4.4367, 'p50_ms': 5.0135, 'p90_ms': 5.4723, 'p99_ms': 8.4541, 'p99.9_ms': 12.2552, 'max_cum_ms': 27.7519}
W guard_queue_ns: {'n': 26883, 'mean_ms': 0.1119, 'p50_ms': 0.108, 'p90_ms': 0.1321, 'p99_ms': 0.1628, 'p99.9_ms': 1.1223, 'max_cum_ms': 16.1001}
W guard_exec_ns: {'n': 26883, 'mean_ms': 3.589, 'p50_ms': 4.2271, 'p90_ms': 4.4892, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 8.1548}
W t_admit_ns: {'n': 26985, 'mean_ms': 0.0937, 'p50_ms': 0.0865, 'p90_ms': 0.107, 'p99_ms': 0.1382, 'p99.9_ms': 1.0732, 'max_cum_ms': 21.7487}
W release_processing_ns: {'n': 4101800, 'mean_ms': 0.0643, 'p50_ms': 0.0632, 'p90_ms': 0.0824, 'p99_ms': 0.107, 'p99.9_ms': 0.1362, 'max_cum_ms': 5.7792}
W loop_lag_ns: {'n': 53600, 'mean_ms': 0.8244, 'p50_ms': 0.0078, 'p90_ms': 3.1949, 'p99_ms': 8.4541, 'p99.9_ms': 10.5513, 'max_cum_ms': 27.5291}
W audit_batch_write_ns: {'n': 53311, 'mean_ms': 1.0705, 'p50_ms': 1.0035, 'p90_ms': 1.2534, 'p99_ms': 2.5723, 'p99.9_ms': 8.8474, 'max_cum_ms': 40.9418}
W counts: {"admitted": 26985, "audit_enqueued": 53342, "audit_written": 53343, "background_round_trips": 10720, "disposition_ALLOW": 26417, "disposition_BLOCK": 466, "guard_windows": 44271, "lease_refills": 127, "provider_calls": 26417, "provider_connections_opened": 4091, "requests_by_round_trips{n=\"0\"}": 26858, "requests_by_round_trips{n=\"1\"}": 127, "shared_state_round_trips": 127, "shed{reason=\"guard_queue\"}": 105}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 0.102}, "per_proc_util": {"nginx": [0.0, 0.005, 0.005, 0.005, 0.006, 0.006, 0.006, 0.006, 0.006, 0.006, 0.007, 0.007, 0.007, 0.007, 0.008, 0.008, 0.008]}, "per_core_util": {"max": 0.009, "mean": 0.007, "sum": 0.11, "n": 16}, "nic": null, "restarted": [], "loadavg_max": 0.25} nginx cpu-ms/req 1.14
redis: ops/s 775.8 ops/req 8.62 cpu cores 0.006 clients 255 mem 1071.5MB ping(us) {'n': 28439, 'p50_us': 432.1, 'p90_us': 482.7, 'p99_us': 580.5, 'p99.9_us': 2403.0, 'max_us': 5226.3, 'mean_us': 448.1}
redis cmdstats: {'info': {'calls_per_s': 0.0, 'usec_per_call': 125.0}, 'ping': {'calls_per_s': 94.6, 'usec_per_call': 0.11}, 'evalsha': {'calls_per_s': 0.4, 'usec_per_call': 11.69}, 'xadd': {'calls_per_s': 177.6, 'usec_per_call': 3.62}, 'mget': {'calls_per_s': 143.5, 'usec_per_call': 0.38}, 'hgetall': {'calls_per_s': 358.8, 'usec_per_call': 0.2}, 'get': {'calls_per_s': 0.4, 'usec_per_call': 0.64}, 'decrby': {'calls_per_s': 0.4, 'usec_per_call': 0.43}}
wire: {'requests_in_window': 27000, 'unit_ip_bytes_per_req': {'unit_to_client_side': 55833.5, 'client_side_to_unit': 8179.5, 'unit_to_provider': 8917.7, 'provider_to_unit': 56513.8, 'unit_to_redis': 5543.9, 'redis_to_unit': 365.5}, 'edge_ip_bytes_per_req': {'edge_to_clients': 55955.2, 'clients_to_edge': 8365.1}, 'olg_resp_body_bytes_mean': {'sse': 67187.3, 'json': 1427.0}}
