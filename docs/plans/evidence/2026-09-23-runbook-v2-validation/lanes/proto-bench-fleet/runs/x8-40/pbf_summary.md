# x8-40: strict FAIL | load-knee PASS (sut, units=1, rate=40)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 2400 (40.0/s) qualified 2364 (39.4/s) FP-blocks 36 (0.015) infra 0 (0.0) drops 0 safety 0
infra reasons: {}
infra detail: {}

T_fw_addon: n=2364 p50=10.7637 p90=15.9959 p99=54.8928 p99.9=73.0617 max=74.3637 mean=13.0968
T_fw_addon_nohold: n=2364 p50=10.5136 p90=13.9566 p99=17.1205 p99.9=22.8944 max=23.9203 mean=10.1216
T_fw_addon_sse: n=1652 p50=10.8993 p90=30.9979 p99=56.5149 p99.9=73.6309 max=74.3637 mean=14.3868
T_fw_addon_json: n=712 p50=10.552 p90=14.0637 p99=17.0257 p99.9=20.2276 max=20.2276 mean=10.1038
T_addon_first_sse: n=1652 p50=10.4072 p90=14.0538 p99=30.6295 p99.9=45.9563 max=51.463 mean=10.3896
T_addon_total_sse: n=1652 p50=10.4946 p90=13.9015 p99=17.1205 p99.9=23.0261 max=23.9203 mean=10.1293
T_addon_total_json: n=712 p50=10.552 p90=14.0637 p99=17.0257 p99.9=20.2276 max=20.2276 mean=10.1038
T_release_lag_max: n=159 p50=50.5022 p90=56.9223 p99=73.6309 p99.9=74.3637 max=74.3637 mean=50.1592
lateness: n=2400 p50=0.0844 p90=0.093 p99=0.1049 p99.9=0.1315 max=0.1548 mean=0.085
loadgen: [{'vm': 'rv-pbf-lg-4', 'busy_max': 4.8, 'busy_mean': 4.6, 'late_max_us': 208, 'conn_opens': 203, 'max_inflight': 203}]  provider_cpu_busy_max: 16.32140230584087
gateway cores total 1.37 cpu-ms/req {'gateway': 34.848, 'workers': 30.716, 'owners': 4.123}
  rv-pbf-unit-6: cores {'launcher': 0.0, 'owner': 0.162, 'owner0': 0.162, 'redis': 0.001, 'worker': 1.208} worker util max 0.252 per-core max 0.225 mean 0.188 gpu {'0': {'n': 61, 'sm_mean': 11.8, 'sm_p95': 17.0, 'sm_max': 18.0}} t_input_p99 13.4349 per-worker admitted {'n': 6, 'min': 336, 'max': 515, 'mean': 397.3, 'max_over_mean': 1.296, 'sheds_per_worker': [0, 0, 0, 0, 0, 0]}
W t_input_ns: {'n': 2385, 'mean_ms': 7.9357, 'p50_ms': 8.5852, 'p90_ms': 11.3377, 'p99_ms': 13.4349, 'p99.9_ms': 17.9569, 'max_cum_ms': 30.0859}
W t_tokenize_ns: {'n': 2384, 'mean_ms': 3.0071, 'p50_ms': 3.031, 'p90_ms': 4.5548, 'p99_ms': 5.6033, 'p99.9_ms': 7.1107, 'max_cum_ms': 8.4984}
W t_guard_wait_ns: {'n': 2385, 'mean_ms': 4.3234, 'p50_ms': 4.8824, 'p90_ms': 5.4723, 'p99_ms': 7.6349, 'p99.9_ms': 12.6484, 'max_cum_ms': 13.1685}
W guard_owner_rtt_ns: {'n': 2385, 'mean_ms': 4.506, 'p50_ms': 5.079, 'p90_ms': 5.7999, 'p99_ms': 7.8971, 'p99.9_ms': 12.1242, 'max_cum_ms': 16.3363}
W guard_queue_ns: {'n': 2385, 'mean_ms': 0.1128, 'p50_ms': 0.1091, 'p90_ms': 0.1382, 'p99_ms': 0.1731, 'p99.9_ms': 0.8479, 'max_cum_ms': 1.262}
W guard_exec_ns: {'n': 2385, 'mean_ms': 3.6433, 'p50_ms': 4.2926, 'p90_ms': 4.6203, 'p99_ms': 6.6519, 'p99.9_ms': 6.8485, 'max_cum_ms': 9.3512}
W t_admit_ns: {'n': 2384, 'mean_ms': 0.1053, 'p50_ms': 0.0896, 'p90_ms': 0.1162, 'p99_ms': 0.897, 'p99.9_ms': 1.237, 'max_cum_ms': 7.9768}
W release_processing_ns: {'n': 369144, 'mean_ms': 0.0638, 'p50_ms': 0.0632, 'p90_ms': 0.0845, 'p99_ms': 0.1111, 'p99.9_ms': 0.1444, 'max_cum_ms': 1.6978}
W loop_lag_ns: {'n': 3580, 'mean_ms': 0.7652, 'p50_ms': 0.0075, 'p90_ms': 2.8344, 'p99_ms': 8.0937, 'p99.9_ms': 10.9445, 'max_cum_ms': 14.1716}
W audit_batch_write_ns: {'n': 4758, 'mean_ms': 1.0885, 'p50_ms': 1.0035, 'p90_ms': 1.3844, 'p99_ms': 2.2446, 'p99.9_ms': 8.5852, 'max_cum_ms': 12.7105}
W counts: {"admitted": 2384, "audit_enqueued": 4762, "audit_written": 4763, "background_round_trips": 716, "disposition_ALLOW": 2349, "disposition_BLOCK": 36, "guard_windows": 3966, "lease_refills": 34, "provider_calls": 2349, "provider_connections_opened": 228, "requests_by_round_trips{n=\"0\"}": 2350, "requests_by_round_trips{n=\"1\"}": 34, "shared_state_round_trips": 34}
edge: null nginx cpu-ms/req None
redis: ops/s 237.6 ops/req 5.939 cpu cores 0.003 clients 40 mem 36.1MB ping(us) {'n': 5694, 'p50_us': 438.5, 'p90_us': 465.0, 'p99_us': 544.5, 'p99.9_us': 883.2, 'max_us': 1608.9, 'mean_us': 442.8}
redis cmdstats: {'get': {'calls_per_s': 0.6, 'usec_per_call': 0.82}, 'info': {'calls_per_s': 0.0, 'usec_per_call': 116.0}, 'xadd': {'calls_per_s': 79.1, 'usec_per_call': 2.86}, 'hgetall': {'calls_per_s': 44.8, 'usec_per_call': 0.26}, 'ping': {'calls_per_s': 94.0, 'usec_per_call': 0.11}, 'decrby': {'calls_per_s': 0.6, 'usec_per_call': 0.56}, 'mget': {'calls_per_s': 17.9, 'usec_per_call': 0.43}, 'evalsha': {'calls_per_s': 0.6, 'usec_per_call': 12.82}}
wire: {'requests_in_window': 2400, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56334.8, 'client_side_to_unit': 8176.2, 'unit_to_provider': 8822.3, 'provider_to_unit': 57004.8, 'unit_to_redis': 5580.2, 'redis_to_unit': 428.0}, 'olg_resp_body_bytes_mean': {'sse': 67430.1, 'json': 1451.9}}
