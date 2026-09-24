# f8-060-r2: strict FAIL | load-knee PASS (sut, units=1, rate=60)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 18000 (60.0/s) qualified 17684 (58.95/s) FP-blocks 314 (0.01744) infra 2 (0.00011111111111111112) drops 0 safety 0
infra reasons: {'http_503': 2, 'incomplete': 2, 'unjoined': 2, 'disposition_missing': 2, 'stage_canon_missing': 2, 'stage_det_missing': 2, 'stage_sem_missing': 2, 'stage_resolve_missing': 2, 'stage_dispatch_missing': 2, 'stage_out_missing': 2, 'stage_audit_missing': 2}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 2}

T_fw_addon: n=17684 p50=11.141 p90=17.453 p99=54.8239 p99.9=72.5845 max=87.7501 mean=13.5104
T_fw_addon_nohold: n=17684 p50=10.8025 p90=14.2614 p99=19.2431 p99.9=25.9284 max=47.7545 mean=10.4475
T_fw_addon_sse: n=12383 p50=11.265 p90=32.5992 p99=56.4186 p99.9=73.1034 max=87.7501 mean=14.8009
T_fw_addon_json: n=5301 p50=10.8202 p90=14.425 p99=18.876 p99.9=25.4554 max=47.7545 mean=10.4959
T_addon_first_sse: n=12383 p50=10.6902 p90=14.1598 p99=28.5703 p99.9=34.2382 max=52.1272 mean=10.5316
T_addon_total_sse: n=12383 p50=10.7974 p90=14.2004 p99=19.3418 p99.9=25.9875 max=29.8965 mean=10.4268
T_addon_total_json: n=5301 p50=10.8202 p90=14.425 p99=18.876 p99.9=25.4554 max=47.7545 mean=10.4959
T_release_lag_max: n=1240 p50=50.5633 p90=56.4168 p99=73.1034 p99.9=86.1601 max=87.7501 mean=50.446
lateness: n=18000 p50=0.0877 p90=0.0974 p99=0.1083 p99.9=0.1301 max=0.2705 mean=0.0876
loadgen: [{'vm': 'rv-pbf-lg-4', 'busy_max': 18.9, 'busy_mean': 5.4, 'late_max_us': 587, 'conn_opens': 315, 'max_inflight': 306}]  provider_cpu_busy_max: 19.728851761516854
gateway cores total 2.06 cpu-ms/req {'gateway': 34.433, 'workers': 30.381, 'owners': 4.046}
  rv-pbf-unit-6: cores {'launcher': 0.0, 'owner': 0.242, 'owner0': 0.242, 'redis': 0.001, 'worker': 1.817} worker util max 0.347 per-core max 0.274 mean 0.265 gpu {'0': {'n': 298, 'sm_mean': 18.5, 'sm_p95': 24.0, 'sm_max': 26.0}} t_input_p99 14.3524 per-worker admitted {'n': 6, 'min': 2603, 'max': 3525, 'mean': 2995.3, 'max_over_mean': 1.177, 'sheds_per_worker': [0, 0, 0, 0, 1, 1]}
W t_input_ns: {'n': 17970, 'mean_ms': 8.0874, 'p50_ms': 8.7163, 'p90_ms': 11.3377, 'p99_ms': 14.3524, 'p99.9_ms': 19.5297, 'max_cum_ms': 41.5419}
W t_tokenize_ns: {'n': 17972, 'mean_ms': 3.1614, 'p50_ms': 3.1621, 'p90_ms': 5.0135, 'p99_ms': 5.931, 'p99.9_ms': 6.6519, 'max_cum_ms': 8.7123}
W t_guard_wait_ns: {'n': 17970, 'mean_ms': 4.3045, 'p50_ms': 4.8824, 'p90_ms': 5.3412, 'p99_ms': 7.6349, 'p99.9_ms': 14.6145, 'max_cum_ms': 34.5976}
W guard_owner_rtt_ns: {'n': 17970, 'mean_ms': 4.4824, 'p50_ms': 5.079, 'p90_ms': 5.6033, 'p99_ms': 7.8971, 'p99.9_ms': 13.1727, 'max_cum_ms': 32.8593}
W guard_queue_ns: {'n': 17970, 'mean_ms': 0.11, 'p50_ms': 0.106, 'p90_ms': 0.1362, 'p99_ms': 0.1731, 'p99.9_ms': 0.7496, 'max_cum_ms': 6.4767}
W guard_exec_ns: {'n': 17970, 'mean_ms': 3.5904, 'p50_ms': 4.2926, 'p90_ms': 4.4892, 'p99_ms': 6.6519, 'p99.9_ms': 6.783, 'max_cum_ms': 9.3512}
W t_admit_ns: {'n': 17972, 'mean_ms': 0.1049, 'p50_ms': 0.0896, 'p90_ms': 0.1193, 'p99_ms': 0.8561, 'p99.9_ms': 1.1551, 'max_cum_ms': 12.8768}
W release_processing_ns: {'n': 2754804, 'mean_ms': 0.0647, 'p50_ms': 0.0632, 'p90_ms': 0.0865, 'p99_ms': 0.1152, 'p99.9_ms': 0.1485, 'max_cum_ms': 30.7951}
W loop_lag_ns: {'n': 17820, 'mean_ms': 0.9199, 'p50_ms': 0.0078, 'p90_ms': 3.883, 'p99_ms': 10.027, 'p99.9_ms': 11.862, 'max_cum_ms': 18.967}
W audit_batch_write_ns: {'n': 35576, 'mean_ms': 1.1415, 'p50_ms': 1.0035, 'p90_ms': 1.3681, 'p99_ms': 5.5378, 'p99.9_ms': 11.5999, 'max_cum_ms': 40.0867}
W counts: {"admitted": 17972, "audit_enqueued": 35657, "audit_written": 35657, "background_round_trips": 3564, "disposition_ALLOW": 17656, "disposition_BLOCK": 314, "guard_windows": 29569, "lease_refills": 248, "provider_calls": 17656, "provider_connections_opened": 1993, "requests_by_round_trips{n=\"0\"}": 17724, "requests_by_round_trips{n=\"1\"}": 248, "shared_state_round_trips": 248, "shed{reason=\"guard_queue\"}": 2}
edge: null nginx cpu-ms/req None
redis: ops/s 278.3 ops/req 4.639 cpu cores 0.003 clients 46 mem 767.2MB ping(us) {'n': 28380, 'p50_us': 455.8, 'p90_us': 523.3, 'p99_us': 572.3, 'p99.9_us': 2037.3, 'max_us': 4046.9, 'mean_us': 473.3}
redis cmdstats: {'get': {'calls_per_s': 0.8, 'usec_per_call': 0.64}, 'info': {'calls_per_s': 0.0, 'usec_per_call': 132.0}, 'xadd': {'calls_per_s': 118.9, 'usec_per_call': 2.71}, 'hgetall': {'calls_per_s': 44.6, 'usec_per_call': 0.24}, 'ping': {'calls_per_s': 94.4, 'usec_per_call': 0.11}, 'decrby': {'calls_per_s': 0.8, 'usec_per_call': 0.49}, 'mget': {'calls_per_s': 17.9, 'usec_per_call': 0.42}, 'evalsha': {'calls_per_s': 0.8, 'usec_per_call': 11.84}}
wire: {'requests_in_window': 18000, 'unit_ip_bytes_per_req': {'unit_to_client_side': -177541.6, 'client_side_to_unit': -25862.4, 'unit_to_provider': -27501.1, 'provider_to_unit': -179693.0, 'unit_to_redis': -19151.4, 'redis_to_unit': -1553.9}, 'olg_resp_body_bytes_mean': {'sse': 67548.2, 'json': 1421.2}}
