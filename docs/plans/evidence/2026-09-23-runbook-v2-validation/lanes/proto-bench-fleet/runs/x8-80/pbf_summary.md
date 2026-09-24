# x8-80: strict FAIL | load-knee FAIL (sut, units=1, rate=80)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': False, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 4800 (80.0/s) qualified 4670 (77.83/s) FP-blocks 81 (0.01688) infra 49 (0.010208333333333333) drops 0 safety 0
infra reasons: {'http_503': 49, 'incomplete': 49, 'unjoined': 49, 'disposition_missing': 49, 'stage_canon_missing': 49, 'stage_det_missing': 49, 'stage_sem_missing': 49, 'stage_resolve_missing': 49, 'stage_dispatch_missing': 49, 'stage_out_missing': 49, 'stage_audit_missing': 49}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 49}

T_fw_addon: n=4670 p50=11.3237 p90=19.872 p99=59.6948 p99.9=73.0103 max=80.2288 mean=14.0133
T_fw_addon_nohold: n=4670 p50=10.9937 p90=14.6763 p99=20.7941 p99.9=26.9878 max=30.3849 mean=10.7165
T_fw_addon_sse: n=3266 p50=11.5011 p90=33.4911 p99=66.5314 p99.9=75.4733 max=80.2288 mean=15.3669
T_fw_addon_json: n=1404 p50=11.0947 p90=14.6906 p99=21.8252 p99.9=28.954 max=30.3849 mean=10.8648
T_addon_first_sse: n=3266 p50=10.8289 p90=15.0667 p99=30.6977 p99.9=35.4554 max=43.0892 mean=10.8442
T_addon_total_sse: n=3266 p50=10.9386 p90=14.6735 p99=20.3801 p99.9=26.7225 max=27.7187 mean=10.6528
T_addon_total_json: n=1404 p50=11.0947 p90=14.6906 p99=21.8252 p99.9=28.954 max=30.3849 mean=10.8648
T_release_lag_max: n=332 p50=51.0182 p90=66.4807 p99=75.4733 p99.9=80.2288 max=80.2288 mean=51.8844
lateness: n=4800 p50=0.0868 p90=0.0966 p99=0.1075 p99.9=0.136 max=0.1687 mean=0.0868
loadgen: [{'vm': 'rv-pbf-lg-4', 'busy_max': 6.6, 'busy_mean': 6.1, 'late_max_us': 329, 'conn_opens': 396, 'max_inflight': 396}]  provider_cpu_busy_max: 13.128943682335969
gateway cores total 2.66 cpu-ms/req {'gateway': 33.83, 'workers': 29.817, 'owners': 4.009}
  rv-pbf-unit-6: cores {'launcher': 0.0, 'owner': 0.315, 'owner0': 0.315, 'redis': 0.001, 'worker': 2.346} worker util max 0.46 per-core max 0.349 mean 0.338 gpu {'0': {'n': 61, 'sm_mean': 24.1, 'sm_p95': 29.0, 'sm_max': 31.0}} t_input_p99 16.7117 per-worker admitted {'n': 6, 'min': 690, 'max': 966, 'mean': 791.0, 'max_over_mean': 1.221, 'sheds_per_worker': [3, 6, 7, 8, 9, 16]}
W t_input_ns: {'n': 4697, 'mean_ms': 8.2572, 'p50_ms': 8.7163, 'p90_ms': 11.7309, 'p99_ms': 16.7117, 'p99.9_ms': 21.1026, 'max_cum_ms': 30.0859}
W t_tokenize_ns: {'n': 4746, 'mean_ms': 3.2627, 'p50_ms': 3.2276, 'p90_ms': 5.3412, 'p99_ms': 6.3242, 'p99.9_ms': 6.9796, 'max_cum_ms': 8.4984}
W t_guard_wait_ns: {'n': 4697, 'mean_ms': 4.3651, 'p50_ms': 4.8824, 'p90_ms': 5.4723, 'p99_ms': 11.2067, 'p99.9_ms': 15.532, 'max_cum_ms': 18.5382}
W guard_owner_rtt_ns: {'n': 4697, 'mean_ms': 4.5353, 'p50_ms': 5.079, 'p90_ms': 5.6689, 'p99_ms': 10.4202, 'p99.9_ms': 14.2213, 'max_cum_ms': 16.3363}
W guard_queue_ns: {'n': 4697, 'mean_ms': 0.1127, 'p50_ms': 0.1039, 'p90_ms': 0.1341, 'p99_ms': 0.1731, 'p99.9_ms': 2.1135, 'max_cum_ms': 5.0554}
W guard_exec_ns: {'n': 4697, 'mean_ms': 3.5944, 'p50_ms': 4.2926, 'p90_ms': 4.4892, 'p99_ms': 6.5864, 'p99.9_ms': 6.8485, 'max_cum_ms': 9.3512}
W t_admit_ns: {'n': 4746, 'mean_ms': 0.101, 'p50_ms': 0.0886, 'p90_ms': 0.1132, 'p99_ms': 0.8397, 'p99.9_ms': 1.1059, 'max_cum_ms': 7.9768}
W release_processing_ns: {'n': 714095, 'mean_ms': 0.0647, 'p50_ms': 0.0632, 'p90_ms': 0.0865, 'p99_ms': 0.1162, 'p99.9_ms': 0.1505, 'max_cum_ms': 3.3799}
W loop_lag_ns: {'n': 3540, 'mean_ms': 0.9537, 'p50_ms': 0.0116, 'p90_ms': 3.8502, 'p99_ms': 10.8134, 'p99.9_ms': 13.3038, 'max_cum_ms': 14.4387}
W audit_batch_write_ns: {'n': 9303, 'mean_ms': 1.2769, 'p50_ms': 1.0363, 'p90_ms': 1.5155, 'p99_ms': 7.0451, 'p99.9_ms': 12.7795, 'max_cum_ms': 21.2035}
W counts: {"admitted": 4746, "audit_enqueued": 9338, "audit_written": 9338, "background_round_trips": 708, "disposition_ALLOW": 4618, "disposition_BLOCK": 79, "guard_windows": 7740, "lease_refills": 66, "provider_calls": 4618, "provider_connections_opened": 492, "requests_by_round_trips{n=\"0\"}": 4680, "requests_by_round_trips{n=\"1\"}": 66, "shared_state_round_trips": 66, "shed{reason=\"guard_queue\"}": 49}
edge: null nginx cpu-ms/req None
redis: ops/s 315.3 ops/req 3.941 cpu cores 0.004 clients 46 mem 125.6MB ping(us) {'n': 5652, 'p50_us': 511.6, 'p90_us': 537.2, 'p99_us': 609.3, 'p99.9_us': 1794.4, 'max_us': 3846.2, 'mean_us': 517.1}
redis cmdstats: {'get': {'calls_per_s': 1.1, 'usec_per_call': 0.65}, 'info': {'calls_per_s': 0.0, 'usec_per_call': 135.0}, 'xadd': {'calls_per_s': 156.2, 'usec_per_call': 2.65}, 'hgetall': {'calls_per_s': 44.6, 'usec_per_call': 0.25}, 'ping': {'calls_per_s': 93.3, 'usec_per_call': 0.09}, 'decrby': {'calls_per_s': 1.1, 'usec_per_call': 0.35}, 'mget': {'calls_per_s': 17.9, 'usec_per_call': 0.42}, 'evalsha': {'calls_per_s': 1.1, 'usec_per_call': 11.08}}
wire: {'requests_in_window': 4800, 'unit_ip_bytes_per_req': {'unit_to_client_side': 55112.9, 'client_side_to_unit': 7884.0, 'unit_to_provider': 8365.0, 'provider_to_unit': 55766.3, 'unit_to_redis': 5379.5, 'redis_to_unit': 334.7}, 'olg_resp_body_bytes_mean': {'sse': 66986.1, 'json': 1428.0}}
