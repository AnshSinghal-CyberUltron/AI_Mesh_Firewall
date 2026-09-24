# f8-070: strict FAIL | load-knee FAIL (sut, units=1, rate=70)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': False, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 21000 (70.0/s) qualified 20596 (68.65/s) FP-blocks 375 (0.01786) infra 29 (0.001380952380952381) drops 0 safety 0
infra reasons: {'http_503': 29, 'incomplete': 29, 'unjoined': 29, 'disposition_missing': 29, 'stage_canon_missing': 29, 'stage_det_missing': 29, 'stage_sem_missing': 29, 'stage_resolve_missing': 29, 'stage_dispatch_missing': 29, 'stage_out_missing': 29, 'stage_audit_missing': 29}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 29}

T_fw_addon: n=20596 p50=11.2539 p90=17.8263 p99=55.3555 p99.9=72.1438 max=78.3078 mean=13.677
T_fw_addon_nohold: n=20596 p50=10.8217 p90=14.475 p99=20.2992 p99.9=25.8297 max=33.4146 mean=10.547
T_fw_addon_sse: n=14418 p50=11.4466 p90=32.4157 p99=57.2745 p99.9=73.3499 max=78.3078 mean=14.9818
T_fw_addon_json: n=6178 p50=10.8923 p90=14.5878 p99=20.1433 p99.9=26.2801 max=33.4146 mean=10.6317
T_addon_first_sse: n=14418 p50=10.8382 p90=14.6674 p99=28.3798 p99.9=33.8291 max=50.5665 mean=10.726
T_addon_total_sse: n=14418 p50=10.7992 p90=14.4382 p99=20.302 p99.9=25.7929 max=29.4664 mean=10.5107
T_addon_total_json: n=6178 p50=10.8923 p90=14.5878 p99=20.1433 p99.9=26.2801 max=33.4146 mean=10.6317
T_release_lag_max: n=1429 p50=50.641 p90=57.3426 p99=73.3499 p99.9=76.2984 max=78.3078 mean=50.6894
lateness: n=21000 p50=0.085 p90=0.0945 p99=0.1052 p99.9=0.1252 max=0.3445 mean=0.0837
loadgen: [{'vm': 'rv-pbf-lg-4', 'busy_max': 6.1, 'busy_mean': 5.7, 'late_max_us': 586, 'conn_opens': 369, 'max_inflight': 355}]  provider_cpu_busy_max: 19.899187546717833
gateway cores total 2.37 cpu-ms/req {'gateway': 33.964, 'workers': 29.931, 'owners': 4.028}
  rv-pbf-unit-6: cores {'launcher': 0.0, 'owner': 0.281, 'owner0': 0.281, 'redis': 0.001, 'worker': 2.088} worker util max 0.419 per-core max 0.307 mean 0.301 gpu {'0': {'n': 298, 'sm_mean': 21.0, 'sm_p95': 26.0, 'sm_max': 30.0}} t_input_p99 14.4835 per-worker admitted {'n': 6, 'min': 2605, 'max': 4516, 'mean': 3499.3, 'max_over_mean': 1.291, 'sheds_per_worker': [1, 4, 4, 4, 6, 10]}
W t_input_ns: {'n': 20968, 'mean_ms': 8.134, 'p50_ms': 8.7163, 'p90_ms': 11.4688, 'p99_ms': 14.4835, 'p99.9_ms': 20.5783, 'max_cum_ms': 41.5419}
W t_tokenize_ns: {'n': 20996, 'mean_ms': 3.2082, 'p50_ms': 3.1949, 'p90_ms': 5.1446, 'p99_ms': 6.1276, 'p99.9_ms': 6.6519, 'max_cum_ms': 8.4984}
W t_guard_wait_ns: {'n': 20968, 'mean_ms': 4.3032, 'p50_ms': 4.8824, 'p90_ms': 5.3412, 'p99_ms': 7.6349, 'p99.9_ms': 14.8767, 'max_cum_ms': 34.5976}
W guard_owner_rtt_ns: {'n': 20967, 'mean_ms': 4.4842, 'p50_ms': 5.079, 'p90_ms': 5.5378, 'p99_ms': 7.8316, 'p99.9_ms': 13.8281, 'max_cum_ms': 32.8593}
W guard_queue_ns: {'n': 20967, 'mean_ms': 0.1112, 'p50_ms': 0.1039, 'p90_ms': 0.1341, 'p99_ms': 0.1731, 'p99.9_ms': 1.2861, 'max_cum_ms': 6.4767}
W guard_exec_ns: {'n': 20967, 'mean_ms': 3.5851, 'p50_ms': 4.2926, 'p90_ms': 4.4892, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 9.3512}
W t_admit_ns: {'n': 20996, 'mean_ms': 0.1007, 'p50_ms': 0.0865, 'p90_ms': 0.1121, 'p99_ms': 0.8315, 'p99.9_ms': 1.1059, 'max_cum_ms': 12.8768}
W release_processing_ns: {'n': 3210744, 'mean_ms': 0.0644, 'p50_ms': 0.0632, 'p90_ms': 0.0865, 'p99_ms': 0.1152, 'p99.9_ms': 0.1485, 'max_cum_ms': 30.7951}
W loop_lag_ns: {'n': 17860, 'mean_ms': 0.9488, 'p50_ms': 0.0104, 'p90_ms': 3.7519, 'p99_ms': 10.4202, 'p99.9_ms': 12.6484, 'max_cum_ms': 18.967}
W audit_batch_write_ns: {'n': 41484, 'mean_ms': 1.2154, 'p50_ms': 1.0281, 'p90_ms': 1.45, 'p99_ms': 6.4553, 'p99.9_ms': 12.2552, 'max_cum_ms': 21.7303}
W counts: {"admitted": 20996, "audit_enqueued": 41602, "audit_written": 41602, "background_round_trips": 3572, "disposition_ALLOW": 20593, "disposition_BLOCK": 375, "guard_windows": 34471, "lease_refills": 291, "provider_calls": 20593, "provider_connections_opened": 2295, "requests_by_round_trips{n=\"0\"}": 20705, "requests_by_round_trips{n=\"1\"}": 291, "shared_state_round_trips": 291, "shed{reason=\"guard_queue\"}": 29}
edge: null nginx cpu-ms/req None
redis: ops/s 298.5 ops/req 4.264 cpu cores 0.003 clients 46 mem 430.5MB ping(us) {'n': 28421, 'p50_us': 450.8, 'p90_us': 476.0, 'p99_us': 528.0, 'p99.9_us': 2087.0, 'max_us': 4452.1, 'mean_us': 457.0}
redis cmdstats: {'get': {'calls_per_s': 1.0, 'usec_per_call': 0.63}, 'info': {'calls_per_s': 0.0, 'usec_per_call': 121.0}, 'xadd': {'calls_per_s': 138.5, 'usec_per_call': 2.66}, 'hgetall': {'calls_per_s': 44.6, 'usec_per_call': 0.25}, 'ping': {'calls_per_s': 94.6, 'usec_per_call': 0.1}, 'decrby': {'calls_per_s': 1.0, 'usec_per_call': 0.4}, 'mget': {'calls_per_s': 17.9, 'usec_per_call': 0.43}, 'evalsha': {'calls_per_s': 1.0, 'usec_per_call': 11.95}}
wire: {'requests_in_window': 21000, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56138.2, 'client_side_to_unit': 8022.0, 'unit_to_provider': 8500.9, 'provider_to_unit': 56828.4, 'unit_to_redis': 5481.3, 'redis_to_unit': 352.7}, 'olg_resp_body_bytes_mean': {'sse': 67447.4, 'json': 1417.9}}
