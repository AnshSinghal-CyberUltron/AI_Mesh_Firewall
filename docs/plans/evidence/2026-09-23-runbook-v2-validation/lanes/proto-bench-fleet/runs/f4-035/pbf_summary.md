# f4-035: strict FAIL | load-knee PASS (sut, units=1, rate=35)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 10500 (35.0/s) qualified 10327 (34.42/s) FP-blocks 173 (0.01648) infra 0 (0.0) drops 0 safety 0
infra reasons: {}
infra detail: {}

T_fw_addon: n=10327 p50=10.9732 p90=17.3537 p99=55.0829 p99.9=71.7382 max=86.3328 mean=13.481
T_fw_addon_nohold: n=10327 p50=10.698 p90=14.2874 p99=19.2307 p99.9=24.4257 max=32.6063 mean=10.3938
T_fw_addon_sse: n=7228 p50=11.0975 p90=33.3457 p99=56.9631 p99.9=72.4854 max=86.3328 mean=14.7634
T_fw_addon_json: n=3099 p50=10.7304 p90=14.6179 p99=19.2792 p99.9=25.1364 max=32.6063 mean=10.49
T_addon_first_sse: n=7228 p50=10.5408 p90=14.1976 p99=30.4752 p99.9=39.1805 max=53.0539 mean=10.4912
T_addon_total_sse: n=7228 p50=10.6832 p90=14.1802 p99=18.9843 p99.9=24.3037 max=30.5346 mean=10.3525
T_addon_total_json: n=3099 p50=10.7304 p90=14.6179 p99=19.2792 p99.9=25.1364 max=32.6063 mean=10.49
T_release_lag_max: n=711 p50=50.7653 p90=57.3177 p99=72.4854 p99.9=86.3328 max=86.3328 mean=51.0859
lateness: n=10500 p50=0.0868 p90=0.096 p99=0.1057 p99.9=0.126 max=0.2332 mean=0.0855
loadgen: [{'vm': 'rv-pbf-lg-4', 'busy_max': 15.7, 'busy_mean': 4.6, 'late_max_us': 450, 'conn_opens': 202, 'max_inflight': 189}]  provider_cpu_busy_max: 17.160974187969547
gateway cores total 1.18 cpu-ms/req {'gateway': 33.807, 'workers': 29.701, 'owners': 4.099}
  rv-pbf-unit-7: cores {'launcher': 0.0, 'owner': 0.143, 'owner0': 0.143, 'redis': 0.001, 'worker': 1.036} worker util max 0.383 per-core max 0.31 mean 0.308 gpu {'0': {'n': 298, 'sm_mean': 10.4, 'sm_p95': 14.0, 'sm_max': 18.0}} t_input_p99 14.3524 per-worker admitted {'n': 3, 'min': 3130, 'max': 4030, 'mean': 3500.0, 'max_over_mean': 1.151, 'sheds_per_worker': [0, 0, 0]}
W t_input_ns: {'n': 10500, 'mean_ms': 8.0519, 'p50_ms': 8.5852, 'p90_ms': 11.4688, 'p99_ms': 14.3524, 'p99.9_ms': 20.054, 'max_cum_ms': 42.7181}
W t_tokenize_ns: {'n': 10500, 'mean_ms': 3.0795, 'p50_ms': 3.031, 'p90_ms': 4.948, 'p99_ms': 5.931, 'p99.9_ms': 6.783, 'max_cum_ms': 14.5981}
W t_guard_wait_ns: {'n': 10500, 'mean_ms': 4.3586, 'p50_ms': 4.8824, 'p90_ms': 5.5378, 'p99_ms': 7.7005, 'p99.9_ms': 14.7456, 'max_cum_ms': 27.6961}
W guard_owner_rtt_ns: {'n': 10500, 'mean_ms': 4.5374, 'p50_ms': 5.1446, 'p90_ms': 5.7344, 'p99_ms': 7.9626, 'p99.9_ms': 14.0902, 'max_cum_ms': 25.8446}
W guard_queue_ns: {'n': 10500, 'mean_ms': 0.1289, 'p50_ms': 0.1121, 'p90_ms': 0.1505, 'p99_ms': 0.4936, 'p99.9_ms': 1.5647, 'max_cum_ms': 5.5404}
W guard_exec_ns: {'n': 10500, 'mean_ms': 3.621, 'p50_ms': 4.2926, 'p90_ms': 4.5548, 'p99_ms': 6.6519, 'p99.9_ms': 6.783, 'max_cum_ms': 13.2049}
W t_admit_ns: {'n': 10500, 'mean_ms': 0.1104, 'p50_ms': 0.0835, 'p90_ms': 0.1111, 'p99_ms': 0.9708, 'p99.9_ms': 1.2206, 'max_cum_ms': 7.295}
W release_processing_ns: {'n': 1611491, 'mean_ms': 0.0646, 'p50_ms': 0.0637, 'p90_ms': 0.0865, 'p99_ms': 0.1142, 'p99.9_ms': 0.1485, 'max_cum_ms': 27.5335}
W loop_lag_ns: {'n': 8940, 'mean_ms': 0.9414, 'p50_ms': 0.0088, 'p90_ms': 3.9485, 'p99_ms': 10.4202, 'p99.9_ms': 12.1242, 'max_cum_ms': 25.118}
W audit_batch_write_ns: {'n': 20809, 'mean_ms': 1.1526, 'p50_ms': 1.0363, 'p90_ms': 1.4008, 'p99_ms': 4.6203, 'p99.9_ms': 11.5999, 'max_cum_ms': 32.9038}
W counts: {"admitted": 10500, "audit_enqueued": 20866, "audit_written": 20866, "background_round_trips": 1788, "disposition_ALLOW": 10327, "disposition_BLOCK": 173, "guard_windows": 17308, "lease_refills": 289, "provider_calls": 10327, "provider_connections_opened": 987, "requests_by_round_trips{n=\"0\"}": 10211, "requests_by_round_trips{n=\"1\"}": 289, "shared_state_round_trips": 289}
edge: null nginx cpu-ms/req None
redis: ops/s 228.2 ops/req 6.521 cpu cores 0.002 clients 46 mem 563.4MB ping(us) {'n': 28061, 'p50_us': 573.1, 'p90_us': 617.6, 'p99_us': 789.3, 'p99.9_us': 2403.2, 'max_us': 6731.4, 'mean_us': 588.5}
redis cmdstats: {'get': {'calls_per_s': 1.0, 'usec_per_call': 0.64}, 'info': {'calls_per_s': 0.0, 'usec_per_call': 128.0}, 'xadd': {'calls_per_s': 69.4, 'usec_per_call': 3.16}, 'hgetall': {'calls_per_s': 44.7, 'usec_per_call': 0.26}, 'ping': {'calls_per_s': 93.4, 'usec_per_call': 0.11}, 'decrby': {'calls_per_s': 1.0, 'usec_per_call': 0.4}, 'mget': {'calls_per_s': 17.9, 'usec_per_call': 0.47}, 'evalsha': {'calls_per_s': 1.0, 'usec_per_call': 11.42}}
wire: {'requests_in_window': 10500, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56293.1, 'client_side_to_unit': 8285.4, 'unit_to_provider': 9017.7, 'provider_to_unit': 56984.7, 'unit_to_redis': 5570.6, 'redis_to_unit': 431.5}, 'olg_resp_body_bytes_mean': {'sse': 67550.4, 'json': 1426.0}}
