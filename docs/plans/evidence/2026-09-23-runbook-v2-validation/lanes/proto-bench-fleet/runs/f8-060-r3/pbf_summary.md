# f8-060-r3: strict FAIL | load-knee PASS (sut, units=1, rate=60)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 18000 (60.0/s) qualified 17676 (58.92/s) FP-blocks 314 (0.01744) infra 10 (0.0005555555555555556) drops 0 safety 0
infra reasons: {'http_503': 10, 'incomplete': 10, 'unjoined': 10, 'disposition_missing': 10, 'stage_canon_missing': 10, 'stage_det_missing': 10, 'stage_sem_missing': 10, 'stage_resolve_missing': 10, 'stage_dispatch_missing': 10, 'stage_out_missing': 10, 'stage_audit_missing': 10}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 10}

T_fw_addon: n=17676 p50=11.1068 p90=17.2898 p99=55.1742 p99.9=71.3543 max=83.7216 mean=13.4563
T_fw_addon_nohold: n=17676 p50=10.7944 p90=14.2132 p99=19.1446 p99.9=25.0005 max=57.1415 mean=10.4114
T_fw_addon_sse: n=12375 p50=11.228 p90=32.2181 p99=56.6208 p99.9=71.826 max=83.7216 mean=14.7375
T_fw_addon_json: n=5301 p50=10.821 p90=14.3742 p99=19.4912 p99.9=25.6548 max=38.3432 mean=10.4656
T_addon_first_sse: n=12375 p50=10.6663 p90=14.1964 p99=28.4171 p99.9=35.5763 max=56.9062 mean=10.5199
T_addon_total_sse: n=12375 p50=10.7863 p90=14.1658 p99=19.0308 p99.9=24.4712 max=57.1415 mean=10.3882
T_addon_total_json: n=5301 p50=10.821 p90=14.3742 p99=19.4912 p99.9=25.6548 max=38.3432 mean=10.4656
T_release_lag_max: n=1216 p50=50.8326 p90=56.6248 p99=71.826 p99.9=77.4 max=83.7216 mean=50.7019
lateness: n=18000 p50=0.0883 p90=0.0979 p99=0.109 p99.9=0.1278 max=0.3579 mean=0.0885
loadgen: [{'vm': 'rv-pbf-lg-4', 'busy_max': 5.7, 'busy_mean': 5.4, 'late_max_us': 652, 'conn_opens': 314, 'max_inflight': 306}]  provider_cpu_busy_max: 16.475206579246603
gateway cores total 2.06 cpu-ms/req {'gateway': 34.395, 'workers': 30.348, 'owners': 4.042}
  rv-pbf-unit-6: cores {'launcher': 0.0, 'owner': 0.242, 'owner0': 0.242, 'redis': 0.001, 'worker': 1.815} worker util max 0.343 per-core max 0.273 mean 0.264 gpu {'0': {'n': 298, 'sm_mean': 18.5, 'sm_p95': 24.0, 'sm_max': 26.0}} t_input_p99 14.4835 per-worker admitted {'n': 6, 'min': 2551, 'max': 3464, 'mean': 2994.5, 'max_over_mean': 1.157, 'sheds_per_worker': [0, 0, 2, 2, 3, 3]}
W t_input_ns: {'n': 17958, 'mean_ms': 8.0803, 'p50_ms': 8.7163, 'p90_ms': 11.3377, 'p99_ms': 14.4835, 'p99.9_ms': 19.7919, 'max_cum_ms': 41.5419}
W t_tokenize_ns: {'n': 17967, 'mean_ms': 3.1582, 'p50_ms': 3.1621, 'p90_ms': 5.0135, 'p99_ms': 5.931, 'p99.9_ms': 6.8485, 'max_cum_ms': 8.9682}
W t_guard_wait_ns: {'n': 17958, 'mean_ms': 4.3047, 'p50_ms': 4.8824, 'p90_ms': 5.3412, 'p99_ms': 7.7005, 'p99.9_ms': 14.3524, 'max_cum_ms': 34.5976}
W guard_owner_rtt_ns: {'n': 17958, 'mean_ms': 4.4831, 'p50_ms': 5.079, 'p90_ms': 5.6033, 'p99_ms': 7.8971, 'p99.9_ms': 13.3038, 'max_cum_ms': 32.8593}
W guard_queue_ns: {'n': 17958, 'mean_ms': 0.1104, 'p50_ms': 0.106, 'p90_ms': 0.1341, 'p99_ms': 0.169, 'p99.9_ms': 0.9708, 'max_cum_ms': 6.4767}
W guard_exec_ns: {'n': 17958, 'mean_ms': 3.5895, 'p50_ms': 4.2926, 'p90_ms': 4.4892, 'p99_ms': 6.5864, 'p99.9_ms': 6.783, 'max_cum_ms': 9.3512}
W t_admit_ns: {'n': 17967, 'mean_ms': 0.1039, 'p50_ms': 0.0896, 'p90_ms': 0.1172, 'p99_ms': 0.8561, 'p99.9_ms': 1.0895, 'max_cum_ms': 12.8768}
W release_processing_ns: {'n': 2752948, 'mean_ms': 0.0646, 'p50_ms': 0.0632, 'p90_ms': 0.0855, 'p99_ms': 0.1142, 'p99.9_ms': 0.1464, 'max_cum_ms': 30.7951}
W loop_lag_ns: {'n': 17820, 'mean_ms': 0.9278, 'p50_ms': 0.0078, 'p90_ms': 3.9485, 'p99_ms': 10.027, 'p99.9_ms': 11.9931, 'max_cum_ms': 18.967}
W audit_batch_write_ns: {'n': 35546, 'mean_ms': 1.1187, 'p50_ms': 0.9789, 'p90_ms': 1.3353, 'p99_ms': 5.6033, 'p99.9_ms': 10.6824, 'max_cum_ms': 43.4425}
W counts: {"admitted": 17967, "audit_enqueued": 35631, "audit_written": 35631, "background_round_trips": 3564, "disposition_ALLOW": 17645, "disposition_BLOCK": 313, "guard_windows": 29545, "lease_refills": 248, "provider_calls": 17645, "provider_connections_opened": 1880, "requests_by_round_trips{n=\"0\"}": 17719, "requests_by_round_trips{n=\"1\"}": 248, "shared_state_round_trips": 248, "shed{reason=\"guard_queue\"}": 10}
edge: null nginx cpu-ms/req None
redis: ops/s 277.9 ops/req 4.631 cpu cores 0.003 clients 46 mem 889.6MB ping(us) {'n': 28252, 'p50_us': 512.1, 'p90_us': 540.0, 'p99_us': 646.2, 'p99.9_us': 2136.3, 'max_us': 6133.6, 'mean_us': 520.8}
redis cmdstats: {'get': {'calls_per_s': 0.8, 'usec_per_call': 0.72}, 'info': {'calls_per_s': 0.0, 'usec_per_call': 118.0}, 'xadd': {'calls_per_s': 118.9, 'usec_per_call': 2.73}, 'hgetall': {'calls_per_s': 44.6, 'usec_per_call': 0.24}, 'ping': {'calls_per_s': 94.0, 'usec_per_call': 0.1}, 'decrby': {'calls_per_s': 0.8, 'usec_per_call': 0.55}, 'mget': {'calls_per_s': 17.9, 'usec_per_call': 0.43}, 'evalsha': {'calls_per_s': 0.8, 'usec_per_call': 14.35}}
wire: {'requests_in_window': 18000, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56293.3, 'client_side_to_unit': 8059.4, 'unit_to_provider': 8576.3, 'provider_to_unit': 56980.7, 'unit_to_redis': 5515.5, 'redis_to_unit': 369.7}, 'olg_resp_body_bytes_mean': {'sse': 67538.9, 'json': 1421.1}}
