# x4-45: strict FAIL | load-knee FAIL (sut, units=1, rate=45)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 2700 (45.0/s) qualified 2657 (44.28/s) FP-blocks 42 (0.01556) infra 1 (0.00037037037037037035) drops 0 safety 0
infra reasons: {'http_503': 1, 'incomplete': 1, 'unjoined': 1, 'disposition_missing': 1, 'stage_canon_missing': 1, 'stage_det_missing': 1, 'stage_sem_missing': 1, 'stage_resolve_missing': 1, 'stage_dispatch_missing': 1, 'stage_out_missing': 1, 'stage_audit_missing': 1}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 1}

T_fw_addon: n=2657 p50=11.6551 p90=17.8977 p99=56.094 p99.9=73.2935 max=81.1377 mean=13.9953
T_fw_addon_nohold: n=2657 p50=11.0985 p90=15.0708 p99=20.425 p99.9=28.3514 max=32.8516 mean=10.8294
T_fw_addon_sse: n=1857 p50=11.8651 p90=31.7823 p99=57.6934 p99.9=73.9917 max=81.1377 mean=15.4111
T_fw_addon_json: n=800 p50=11.0602 p90=14.7938 p99=18.1511 p99.9=22.9581 max=22.9581 mean=10.7089
T_addon_first_sse: n=1857 p50=11.4467 p90=15.2843 p99=31.2046 p99.9=47.1911 max=62.8828 mean=11.4134
T_addon_total_sse: n=1857 p50=11.0987 p90=15.2241 p99=21.1723 p99.9=28.5903 max=32.8516 mean=10.8813
T_addon_total_json: n=800 p50=11.0602 p90=14.7938 p99=18.1511 p99.9=22.9581 max=22.9581 mean=10.7089
T_release_lag_max: n=172 p50=51.2613 p90=57.6934 p99=73.9917 p99.9=81.1377 max=81.1377 mean=51.1897
lateness: n=2700 p50=0.0872 p90=0.0968 p99=0.1075 p99.9=0.1194 max=0.1247 mean=0.0853
loadgen: [{'vm': 'rv-pbf-lg-4', 'busy_max': 5.0, 'busy_mean': 4.8, 'late_max_us': 289, 'conn_opens': 229, 'max_inflight': 229}]  provider_cpu_busy_max: 16.056307473005482
gateway cores total 1.5 cpu-ms/req {'gateway': 33.799, 'workers': 29.648, 'owners': 4.144}
  rv-pbf-unit-7: cores {'launcher': 0.0, 'owner': 0.183, 'owner0': 0.183, 'redis': 0.001, 'worker': 1.312} worker util max 0.478 per-core max 0.386 mean 0.382 gpu {'0': {'n': 61, 'sm_mean': 14.1, 'sm_p95': 19.0, 'sm_max': 21.0}} t_input_p99 14.8767 per-worker admitted {'n': 3, 'min': 788, 'max': 1006, 'mean': 901.3, 'max_over_mean': 1.116, 'sheds_per_worker': [0, 0, 1]}
W t_input_ns: {'n': 2703, 'mean_ms': 8.3354, 'p50_ms': 8.8474, 'p90_ms': 11.9931, 'p99_ms': 14.8767, 'p99.9_ms': 20.5783, 'max_cum_ms': 42.7181}
W t_tokenize_ns: {'n': 2704, 'mean_ms': 3.2088, 'p50_ms': 3.1949, 'p90_ms': 5.2101, 'p99_ms': 6.1932, 'p99.9_ms': 6.8485, 'max_cum_ms': 14.5981}
W t_guard_wait_ns: {'n': 2703, 'mean_ms': 4.474, 'p50_ms': 4.948, 'p90_ms': 6.2587, 'p99_ms': 8.4541, 'p99.9_ms': 15.0077, 'max_cum_ms': 27.6961}
W guard_owner_rtt_ns: {'n': 2703, 'mean_ms': 4.647, 'p50_ms': 5.1446, 'p90_ms': 6.4553, 'p99_ms': 8.5852, 'p99.9_ms': 13.1727, 'max_cum_ms': 25.8446}
W guard_queue_ns: {'n': 2703, 'mean_ms': 0.1316, 'p50_ms': 0.1121, 'p90_ms': 0.1546, 'p99_ms': 0.5775, 'p99.9_ms': 1.7613, 'max_cum_ms': 5.5404}
W guard_exec_ns: {'n': 2703, 'mean_ms': 3.6585, 'p50_ms': 4.2926, 'p90_ms': 4.6203, 'p99_ms': 6.6519, 'p99.9_ms': 7.5694, 'max_cum_ms': 13.2049}
W t_admit_ns: {'n': 2704, 'mean_ms': 0.1141, 'p50_ms': 0.0855, 'p90_ms': 0.1142, 'p99_ms': 1.0568, 'p99.9_ms': 1.5974, 'max_cum_ms': 7.1759}
W release_processing_ns: {'n': 415385, 'mean_ms': 0.065, 'p50_ms': 0.0637, 'p90_ms': 0.0876, 'p99_ms': 0.1152, 'p99.9_ms': 0.1505, 'max_cum_ms': 6.0855}
W loop_lag_ns: {'n': 1800, 'mean_ms': 0.9706, 'p50_ms': 0.0095, 'p90_ms': 4.1452, 'p99_ms': 10.8134, 'p99.9_ms': 12.7795, 'max_cum_ms': 15.8214}
W audit_batch_write_ns: {'n': 5342, 'mean_ms': 1.3008, 'p50_ms': 1.1059, 'p90_ms': 1.6138, 'p99_ms': 6.5864, 'p99.9_ms': 14.2213, 'max_cum_ms': 30.8953}
W counts: {"admitted": 2704, "audit_enqueued": 5368, "audit_written": 5368, "background_round_trips": 360, "disposition_ALLOW": 2661, "disposition_BLOCK": 42, "guard_windows": 4483, "lease_refills": 75, "provider_calls": 2661, "provider_connections_opened": 233, "requests_by_round_trips{n=\"0\"}": 2629, "requests_by_round_trips{n=\"1\"}": 75, "shared_state_round_trips": 75, "shed{reason=\"guard_queue\"}": 1}
edge: null nginx cpu-ms/req None
redis: ops/s 248.6 ops/req 5.525 cpu cores 0.003 clients 46 mem 165.7MB ping(us) {'n': 5678, 'p50_us': 437.4, 'p90_us': 485.8, 'p99_us': 777.1, 'p99.9_us': 2459.3, 'max_us': 4042.4, 'mean_us': 456.3}
redis cmdstats: {'get': {'calls_per_s': 1.2, 'usec_per_call': 0.65}, 'info': {'calls_per_s': 0.0, 'usec_per_call': 131.0}, 'xadd': {'calls_per_s': 88.5, 'usec_per_call': 2.96}, 'hgetall': {'calls_per_s': 44.8, 'usec_per_call': 0.25}, 'ping': {'calls_per_s': 93.7, 'usec_per_call': 0.1}, 'decrby': {'calls_per_s': 1.2, 'usec_per_call': 0.39}, 'mget': {'calls_per_s': 17.9, 'usec_per_call': 0.47}, 'evalsha': {'calls_per_s': 1.2, 'usec_per_call': 12.88}}
wire: {'requests_in_window': 2700, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56067.5, 'client_side_to_unit': 8272.3, 'unit_to_provider': 8872.2, 'provider_to_unit': 56756.6, 'unit_to_redis': 5469.5, 'redis_to_unit': 389.4}, 'olg_resp_body_bytes_mean': {'sse': 68037.2, 'json': 1454.3}}
