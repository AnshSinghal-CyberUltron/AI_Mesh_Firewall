# f8-060: strict FAIL | load-knee PASS (sut, units=1, rate=60)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 18000 (60.0/s) qualified 17682 (58.94/s) FP-blocks 311 (0.01728) infra 7 (0.00038888888888888887) drops 0 safety 0
infra reasons: {'http_503': 7, 'incomplete': 7, 'unjoined': 7, 'disposition_missing': 7, 'stage_canon_missing': 7, 'stage_det_missing': 7, 'stage_sem_missing': 7, 'stage_resolve_missing': 7, 'stage_dispatch_missing': 7, 'stage_out_missing': 7, 'stage_audit_missing': 7}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 7}

T_fw_addon: n=17682 p50=11.0971 p90=17.9738 p99=54.7857 p99.9=72.0317 max=76.6617 mean=13.6065
T_fw_addon_nohold: n=17682 p50=10.7562 p90=14.1652 p99=19.1977 p99.9=24.9632 max=47.496 mean=10.4041
T_fw_addon_sse: n=12379 p50=11.2359 p90=37.3092 p99=56.0074 p99.9=72.7152 max=76.6617 mean=14.9636
T_fw_addon_json: n=5303 p50=10.7579 p90=14.2582 p99=19.1119 p99.9=24.8291 max=27.9449 mean=10.4387
T_addon_first_sse: n=12379 p50=10.6381 p90=14.181 p99=30.5015 p99.9=38.9186 max=53.8791 mean=10.5435
T_addon_total_sse: n=12379 p50=10.7562 p90=14.1366 p99=19.1977 p99.9=24.9632 max=47.496 mean=10.3893
T_addon_total_json: n=5303 p50=10.7579 p90=14.2582 p99=19.1119 p99.9=24.8291 max=27.9449 mean=10.4387
T_release_lag_max: n=1300 p50=50.5703 p90=55.7492 p99=72.5082 p99.9=75.4788 max=76.6617 mean=50.2407
lateness: n=18000 p50=0.0871 p90=0.0963 p99=0.1069 p99.9=0.125 max=0.2342 mean=0.0871
loadgen: [{'vm': 'rv-pbf-lg-4', 'busy_max': 16.7, 'busy_mean': 5.4, 'late_max_us': 555, 'conn_opens': 315, 'max_inflight': 306}]  provider_cpu_busy_max: 18.96348489554718
gateway cores total 2.05 cpu-ms/req {'gateway': 34.244, 'workers': 30.2, 'owners': 4.038}
  rv-pbf-unit-6: cores {'launcher': 0.0, 'owner': 0.241, 'owner0': 0.241, 'redis': 0.001, 'worker': 1.806} worker util max 0.391 per-core max 0.272 mean 0.266 gpu {'0': {'n': 298, 'sm_mean': 18.5, 'sm_p95': 24.0, 'sm_max': 26.0}} t_input_p99 14.3524 per-worker admitted {'n': 6, 'min': 2525, 'max': 4166, 'mean': 2999.3, 'max_over_mean': 1.389, 'sheds_per_worker': [0, 0, 0, 1, 2, 4]}
W t_input_ns: {'n': 17990, 'mean_ms': 8.0606, 'p50_ms': 8.5852, 'p90_ms': 11.2067, 'p99_ms': 14.3524, 'p99.9_ms': 20.054, 'max_cum_ms': 38.3032}
W t_tokenize_ns: {'n': 17997, 'mean_ms': 3.1441, 'p50_ms': 3.1293, 'p90_ms': 5.0135, 'p99_ms': 5.931, 'p99.9_ms': 6.5864, 'max_cum_ms': 8.4984}
W t_guard_wait_ns: {'n': 17990, 'mean_ms': 4.2993, 'p50_ms': 4.8824, 'p90_ms': 5.3412, 'p99_ms': 7.766, 'p99.9_ms': 14.7456, 'max_cum_ms': 33.7443}
W guard_owner_rtt_ns: {'n': 17990, 'mean_ms': 4.4778, 'p50_ms': 5.079, 'p90_ms': 5.5378, 'p99_ms': 7.9626, 'p99.9_ms': 13.4349, 'max_cum_ms': 31.3619}
W guard_queue_ns: {'n': 17990, 'mean_ms': 0.1097, 'p50_ms': 0.105, 'p90_ms': 0.1341, 'p99_ms': 0.169, 'p99.9_ms': 0.6185, 'max_cum_ms': 5.0653}
W guard_exec_ns: {'n': 17990, 'mean_ms': 3.5867, 'p50_ms': 4.2926, 'p90_ms': 4.4892, 'p99_ms': 6.5864, 'p99.9_ms': 6.783, 'max_cum_ms': 9.3512}
W t_admit_ns: {'n': 17996, 'mean_ms': 0.1026, 'p50_ms': 0.0876, 'p90_ms': 0.1152, 'p99_ms': 0.8561, 'p99.9_ms': 1.1223, 'max_cum_ms': 11.8898}
W release_processing_ns: {'n': 2761889, 'mean_ms': 0.0644, 'p50_ms': 0.0632, 'p90_ms': 0.0855, 'p99_ms': 0.1142, 'p99.9_ms': 0.1464, 'max_cum_ms': 3.3799}
W loop_lag_ns: {'n': 17870, 'mean_ms': 0.8993, 'p50_ms': 0.008, 'p90_ms': 3.6536, 'p99_ms': 10.027, 'p99.9_ms': 12.7795, 'max_cum_ms': 16.076}
W audit_batch_write_ns: {'n': 35655, 'mean_ms': 1.153, 'p50_ms': 1.0035, 'p90_ms': 1.3681, 'p99_ms': 5.7344, 'p99.9_ms': 11.9931, 'max_cum_ms': 21.2035}
W counts: {"admitted": 17996, "audit_enqueued": 35723, "audit_written": 35722, "background_round_trips": 3574, "disposition_ALLOW": 17678, "disposition_BLOCK": 312, "guard_windows": 29600, "lease_refills": 249, "provider_calls": 17678, "provider_connections_opened": 2000, "requests_by_round_trips{n=\"0\"}": 17747, "requests_by_round_trips{n=\"1\"}": 249, "shared_state_round_trips": 249, "shed{reason=\"guard_queue\"}": 7}
edge: null nginx cpu-ms/req None
redis: ops/s 278.0 ops/req 4.633 cpu cores 0.003 clients 46 mem 287.9MB ping(us) {'n': 28280, 'p50_us': 504.9, 'p90_us': 529.7, 'p99_us': 581.8, 'p99.9_us': 1862.6, 'max_us': 4790.8, 'mean_us': 510.4}
redis cmdstats: {'get': {'calls_per_s': 0.8, 'usec_per_call': 0.67}, 'info': {'calls_per_s': 0.0, 'usec_per_call': 133.0}, 'xadd': {'calls_per_s': 118.9, 'usec_per_call': 2.64}, 'hgetall': {'calls_per_s': 44.6, 'usec_per_call': 0.24}, 'ping': {'calls_per_s': 94.1, 'usec_per_call': 0.1}, 'decrby': {'calls_per_s': 0.8, 'usec_per_call': 0.38}, 'mget': {'calls_per_s': 17.9, 'usec_per_call': 0.41}, 'evalsha': {'calls_per_s': 0.8, 'usec_per_call': 12.24}}
wire: {'requests_in_window': 18000, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56326.8, 'client_side_to_unit': 8104.0, 'unit_to_provider': 8602.2, 'provider_to_unit': 57015.7, 'unit_to_redis': 5517.8, 'redis_to_unit': 370.0}, 'olg_resp_body_bytes_mean': {'sse': 67550.9, 'json': 1421.0}}
