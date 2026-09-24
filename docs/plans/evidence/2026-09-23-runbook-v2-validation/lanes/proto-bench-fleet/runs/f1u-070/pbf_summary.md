# f1u-070: strict FAIL | load-knee PASS (sut, units=1, rate=70)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 21000 (70.0/s) qualified 20690 (68.97/s) FP-blocks 305 (0.01452) infra 5 (0.0002380952380952381) drops 0 safety 0
infra reasons: {'http_503': 5, 'incomplete': 5, 'unjoined': 5, 'disposition_missing': 5, 'stage_canon_missing': 5, 'stage_det_missing': 5, 'stage_sem_missing': 5, 'stage_resolve_missing': 5, 'stage_dispatch_missing': 5, 'stage_out_missing': 5, 'stage_audit_missing': 5}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 5}

T_fw_addon: n=20690 p50=10.6782 p90=15.5748 p99=54.1524 p99.9=71.6238 max=91.4473 mean=13.0597
T_fw_addon_nohold: n=20690 p50=10.4115 p90=13.4768 p99=16.4283 p99.9=20.7015 max=25.5829 mean=9.9076
T_fw_addon_sse: n=14478 p50=10.7671 p90=34.5153 p99=55.4042 p99.9=72.3734 max=91.4473 mean=14.3809
T_fw_addon_json: n=6212 p50=10.4823 p90=13.5726 p99=16.6541 p99.9=20.7015 max=25.5829 mean=9.9803
T_addon_first_sse: n=14478 p50=10.3313 p90=13.6708 p99=28.6441 p99.9=34.7771 max=54.0063 mean=10.0856
T_addon_total_sse: n=14478 p50=10.3792 p90=13.4147 p99=16.3193 p99.9=20.6823 max=24.356 mean=9.8764
T_addon_total_json: n=6212 p50=10.4823 p90=13.5726 p99=16.6541 p99.9=20.7015 max=25.5829 mean=9.9803
T_release_lag_max: n=1505 p50=50.1565 p90=55.1309 p99=72.2666 p99.9=91.3613 max=91.4473 mean=50.0194
lateness: n=21000 p50=0.0864 p90=0.0965 p99=0.1077 p99.9=0.1257 max=0.2032 mean=0.0867
loadgen: [{'vm': 'rv-pbf-lg-1', 'busy_max': 4.4, 'busy_mean': 4.1, 'late_max_us': 307, 'conn_opens': 139, 'max_inflight': 128}, {'vm': 'rv-pbf-lg-2', 'busy_max': 4.4, 'busy_mean': 4.0, 'late_max_us': 175, 'conn_opens': 139, 'max_inflight': 128}, {'vm': 'rv-pbf-lg-3', 'busy_max': 4.5, 'busy_mean': 4.1, 'late_max_us': 203, 'conn_opens': 141, 'max_inflight': 128}]  provider_cpu_busy_max: 25.46321723064906
gateway cores total 2.49 cpu-ms/req {'gateway': 35.624, 'workers': 31.515, 'owners': 4.102}
  rv-pbf-unit-2: cores {'launcher': 0.0, 'owner': 0.286, 'owner0': 0.155, 'owner1': 0.132, 'redis': 0.001, 'worker': 2.199} worker util max 0.146 per-core max 0.125 mean 0.106 gpu {'0': {'n': 299, 'sm_mean': 11.9, 'sm_p95': 21.0, 'sm_max': 29.0}, '1': {'n': 299, 'sm_mean': 9.4, 'sm_p95': 18.0, 'sm_max': 20.0}} t_input_p99 13.0417 per-worker admitted {'n': 18, 'min': 688, 'max': 1425, 'mean': 1165.5, 'max_over_mean': 1.223, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1]}
W t_input_ns: {'n': 20974, 'mean_ms': 7.6713, 'p50_ms': 8.3558, 'p90_ms': 10.4202, 'p99_ms': 13.0417, 'p99.9_ms': 17.1704, 'max_cum_ms': 35.9043}
W t_tokenize_ns: {'n': 20979, 'mean_ms': 2.8679, 'p50_ms': 2.8672, 'p90_ms': 4.4237, 'p99_ms': 5.079, 'p99.9_ms': 6.3898, 'max_cum_ms': 8.9355}
W t_guard_wait_ns: {'n': 20974, 'mean_ms': 4.2515, 'p50_ms': 4.8169, 'p90_ms': 5.2101, 'p99_ms': 7.3073, 'p99.9_ms': 12.1242, 'max_cum_ms': 29.2545}
W guard_owner_rtt_ns: {'n': 20974, 'mean_ms': 4.4295, 'p50_ms': 5.0135, 'p90_ms': 5.4067, 'p99_ms': 7.5694, 'p99.9_ms': 11.5999, 'max_cum_ms': 27.7519}
W guard_queue_ns: {'n': 20974, 'mean_ms': 0.1115, 'p50_ms': 0.1091, 'p90_ms': 0.1321, 'p99_ms': 0.1587, 'p99.9_ms': 0.2324, 'max_cum_ms': 16.1001}
W guard_exec_ns: {'n': 20974, 'mean_ms': 3.611, 'p50_ms': 4.2926, 'p90_ms': 4.4892, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 8.1548}
W t_admit_ns: {'n': 20979, 'mean_ms': 0.0934, 'p50_ms': 0.0876, 'p90_ms': 0.106, 'p99_ms': 0.1321, 'p99.9_ms': 1.0568, 'max_cum_ms': 21.7487}
W release_processing_ns: {'n': 3212001, 'mean_ms': 0.0642, 'p50_ms': 0.0637, 'p90_ms': 0.0824, 'p99_ms': 0.105, 'p99.9_ms': 0.1321, 'max_cum_ms': 5.7792}
W loop_lag_ns: {'n': 53580, 'mean_ms': 0.7903, 'p50_ms': 0.0069, 'p90_ms': 2.7034, 'p99_ms': 7.9626, 'p99.9_ms': 10.4202, 'max_cum_ms': 21.4172}
W audit_batch_write_ns: {'n': 41621, 'mean_ms': 1.0557, 'p50_ms': 0.9871, 'p90_ms': 1.237, 'p99_ms': 2.089, 'p99.9_ms': 8.8474, 'max_cum_ms': 40.9418}
W counts: {"admitted": 20979, "audit_enqueued": 41638, "audit_written": 41638, "background_round_trips": 10716, "disposition_ALLOW": 20669, "disposition_BLOCK": 305, "guard_windows": 34676, "lease_refills": 97, "provider_calls": 20669, "provider_connections_opened": 3021, "requests_by_round_trips{n=\"0\"}": 20882, "requests_by_round_trips{n=\"1\"}": 97, "shared_state_round_trips": 97, "shed{reason=\"guard_queue\"}": 5}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 0.078}, "per_proc_util": {"nginx": [0.0, 0.003, 0.004, 0.004, 0.004, 0.005, 0.005, 0.005, 0.005, 0.005, 0.005, 0.005, 0.005, 0.006, 0.006, 0.006, 0.006]}, "per_core_util": {"max": 0.006, "mean": 0.005, "sum": 0.08, "n": 16}, "nic": null, "restarted": [], "loadavg_max": 0.3} nginx cpu-ms/req 1.114
redis: ops/s 736.4 ops/req 10.52 cpu cores 0.005 clients 255 mem 725.9MB ping(us) {'n': 28289, 'p50_us': 490.4, 'p90_us': 553.7, 'p99_us': 638.2, 'p99.9_us': 2730.6, 'max_us': 4879.0, 'mean_us': 506.8}
redis cmdstats: {'info': {'calls_per_s': 0.0, 'usec_per_call': 144.0}, 'ping': {'calls_per_s': 94.1, 'usec_per_call': 0.1}, 'evalsha': {'calls_per_s': 0.3, 'usec_per_call': 12.29}, 'xadd': {'calls_per_s': 138.8, 'usec_per_call': 3.66}, 'mget': {'calls_per_s': 143.5, 'usec_per_call': 0.38}, 'hgetall': {'calls_per_s': 358.9, 'usec_per_call': 0.2}, 'get': {'calls_per_s': 0.3, 'usec_per_call': 0.72}, 'decrby': {'calls_per_s': 0.3, 'usec_per_call': 0.45}}
wire: {'requests_in_window': 21000, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56249.1, 'client_side_to_unit': 8284.7, 'unit_to_provider': 9087.2, 'provider_to_unit': 56934.7, 'unit_to_redis': 5626.9, 'redis_to_unit': 399.7}, 'edge_ip_bytes_per_req': {'edge_to_clients': 56368.9, 'clients_to_edge': 8501.7}, 'olg_resp_body_bytes_mean': {'sse': 67353.3, 'json': 1432.8}}
