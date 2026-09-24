# x4-10: strict FAIL | load-knee PASS (sut, units=1, rate=10)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 600 (10.0/s) qualified 593 (9.88/s) FP-blocks 7 (0.01167) infra 0 (0.0) drops 0 safety 0
infra reasons: {}
infra detail: {}

T_fw_addon: n=593 p50=10.7513 p90=15.6386 p99=55.0023 p99.9=70.0174 max=70.0174 mean=13.1816
T_fw_addon_nohold: n=593 p50=10.4337 p90=13.7124 p99=16.3024 p99.9=20.1927 max=20.1927 mean=10.0992
T_fw_addon_sse: n=414 p50=10.8974 p90=32.4401 p99=56.559 p99.9=70.0174 max=70.0174 mean=14.5322
T_fw_addon_json: n=179 p50=10.4251 p90=13.6781 p99=16.4279 p99.9=20.1927 max=20.1927 mean=10.0577
T_addon_first_sse: n=414 p50=10.3867 p90=13.7208 p99=30.8451 p99.9=34.6996 max=34.6996 mean=10.3484
T_addon_total_sse: n=414 p50=10.4337 p90=13.7527 p99=16.1109 p99.9=17.8632 max=17.8632 mean=10.1171
T_addon_total_json: n=179 p50=10.4251 p90=13.6781 p99=16.4279 p99.9=20.1927 max=20.1927 mean=10.0577
T_release_lag_max: n=41 p50=50.5122 p90=56.559 p99=70.0174 p99.9=70.0174 max=70.0174 mean=51.2834
lateness: n=600 p50=0.0924 p90=0.1083 p99=0.1237 p99.9=0.1771 max=0.1771 mean=0.094
loadgen: [{'vm': 'rv-pbf-lg-4', 'busy_max': 3.9, 'busy_mean': 3.7, 'late_max_us': 194, 'conn_opens': 57, 'max_inflight': 57}]  provider_cpu_busy_max: 6.240047542060512
gateway cores total 0.35 cpu-ms/req {'gateway': 35.885, 'workers': 31.621, 'owners': 4.238}
  rv-pbf-unit-7: cores {'launcher': 0.0, 'owner': 0.042, 'owner0': 0.042, 'redis': 0.001, 'worker': 0.311} worker util max 0.119 per-core max 0.102 mean 0.094 gpu {'0': {'n': 60, 'sm_mean': 2.6, 'sm_p95': 4.0, 'sm_max': 6.0}} t_input_p99 13.0417 per-worker admitted {'n': 3, 'min': 159, 'max': 244, 'mean': 200.3, 'max_over_mean': 1.218, 'sheds_per_worker': [0, 0, 0]}
W t_input_ns: {'n': 601, 'mean_ms': 7.717, 'p50_ms': 8.4541, 'p90_ms': 10.2892, 'p99_ms': 13.0417, 'p99.9_ms': 17.9569, 'max_cum_ms': 28.678}
W t_tokenize_ns: {'n': 601, 'mean_ms': 2.834, 'p50_ms': 2.8344, 'p90_ms': 4.2926, 'p99_ms': 5.079, 'p99.9_ms': 6.3898, 'max_cum_ms': 6.3871}
W t_guard_wait_ns: {'n': 601, 'mean_ms': 4.2737, 'p50_ms': 4.948, 'p90_ms': 5.2756, 'p99_ms': 7.3728, 'p99.9_ms': 10.027, 'max_cum_ms': 12.1806}
W guard_owner_rtt_ns: {'n': 601, 'mean_ms': 4.4649, 'p50_ms': 5.1446, 'p90_ms': 5.5378, 'p99_ms': 7.6349, 'p99.9_ms': 9.6338, 'max_cum_ms': 16.839}
W guard_queue_ns: {'n': 601, 'mean_ms': 0.1313, 'p50_ms': 0.1244, 'p90_ms': 0.1485, 'p99_ms': 0.1812, 'p99.9_ms': 1.0117, 'max_cum_ms': 1.0077}
W guard_exec_ns: {'n': 601, 'mean_ms': 3.5764, 'p50_ms': 4.2926, 'p90_ms': 4.5548, 'p99_ms': 6.5864, 'p99.9_ms': 7.0451, 'max_cum_ms': 9.0453}
W t_admit_ns: {'n': 601, 'mean_ms': 0.1214, 'p50_ms': 0.0916, 'p90_ms': 0.1213, 'p99_ms': 1.1059, 'p99.9_ms': 1.5647, 'max_cum_ms': 6.6011}
W release_processing_ns: {'n': 93834, 'mean_ms': 0.0632, 'p50_ms': 0.0632, 'p90_ms': 0.0824, 'p99_ms': 0.107, 'p99.9_ms': 0.1382, 'max_cum_ms': 1.2298}
W loop_lag_ns: {'n': 1800, 'mean_ms': 0.6515, 'p50_ms': 0.0188, 'p90_ms': 1.0895, 'p99_ms': 6.9796, 'p99.9_ms': 8.4541, 'max_cum_ms': 10.8941}
W audit_batch_write_ns: {'n': 1194, 'mean_ms': 1.055, 'p50_ms': 0.9462, 'p90_ms': 1.3353, 'p99_ms': 4.3581, 'p99.9_ms': 7.7005, 'max_cum_ms': 10.7314}
W counts: {"admitted": 601, "audit_enqueued": 1195, "audit_written": 1195, "background_round_trips": 360, "disposition_ALLOW": 594, "disposition_BLOCK": 7, "guard_windows": 966, "lease_refills": 16, "provider_calls": 594, "provider_connections_opened": 123, "requests_by_round_trips{n=\"0\"}": 585, "requests_by_round_trips{n=\"1\"}": 16, "shared_state_round_trips": 16}
edge: null nginx cpu-ms/req None
redis: ops/s 176.1 ops/req 17.607 cpu cores 0.002 clients 46 mem 41.7MB ping(us) {'n': 5629, 'p50_us': 555.4, 'p90_us': 643.6, 'p99_us': 706.9, 'p99.9_us': 841.8, 'max_us': 2258.6, 'mean_us': 571.2}
redis cmdstats: {'get': {'calls_per_s': 0.3, 'usec_per_call': 0.69}, 'info': {'calls_per_s': 0.0, 'usec_per_call': 133.0}, 'xadd': {'calls_per_s': 19.7, 'usec_per_call': 4.39}, 'hgetall': {'calls_per_s': 44.7, 'usec_per_call': 0.25}, 'hello': {'calls_per_s': 0.1, 'usec_per_call': 2.67}, 'ping': {'calls_per_s': 92.9, 'usec_per_call': 0.11}, 'decrby': {'calls_per_s': 0.3, 'usec_per_call': 0.31}, 'mget': {'calls_per_s': 17.9, 'usec_per_call': 0.5}, 'evalsha': {'calls_per_s': 0.3, 'usec_per_call': 12.56}}
wire: {'requests_in_window': 600, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56999.4, 'client_side_to_unit': 9402.2, 'unit_to_provider': 10352.5, 'provider_to_unit': 57741.2, 'unit_to_redis': 6089.4, 'redis_to_unit': 882.0}, 'olg_resp_body_bytes_mean': {'sse': 68746.8, 'json': 1413.4}}
