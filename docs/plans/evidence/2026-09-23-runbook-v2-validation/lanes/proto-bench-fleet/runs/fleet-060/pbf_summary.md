# fleet-060: strict FAIL | load-knee PASS (sut, units=3, rate=60)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 18000 (60.0/s) qualified 17689 (58.96/s) FP-blocks 310 (0.01722) infra 1 (5.555555555555556e-05) drops 0 safety 0
infra reasons: {'http_503': 1, 'incomplete': 1, 'unjoined': 1, 'disposition_missing': 1, 'stage_canon_missing': 1, 'stage_det_missing': 1, 'stage_sem_missing': 1, 'stage_resolve_missing': 1, 'stage_dispatch_missing': 1, 'stage_out_missing': 1, 'stage_audit_missing': 1}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 1}

T_fw_addon: n=17689 p50=10.7137 p90=15.6072 p99=53.6203 p99.9=71.2059 max=76.2714 mean=13.0424
T_fw_addon_nohold: n=17689 p50=10.4489 p90=13.3547 p99=16.2229 p99.9=20.2136 max=24.3285 mean=9.9318
T_fw_addon_sse: n=12387 p50=10.8165 p90=33.7041 p99=54.6861 p99.9=71.4961 max=76.2714 mean=14.349
T_fw_addon_json: n=5302 p50=10.4734 p90=13.5366 p99=16.2883 p99.9=20.2136 max=24.3285 mean=9.99
T_addon_first_sse: n=12387 p50=10.375 p90=13.526 p99=28.5731 p99.9=34.0334 max=53.825 mean=10.1106
T_addon_total_sse: n=12387 p50=10.4381 p90=13.2362 p99=16.1263 p99.9=19.9647 max=24.1161 mean=9.9068
T_addon_total_json: n=5302 p50=10.4734 p90=13.5366 p99=16.2883 p99.9=20.2136 max=24.3285 mean=9.99
T_release_lag_max: n=1283 p50=50.0253 p90=54.5858 p99=71.4961 p99.9=74.2893 max=76.2714 mean=49.5327
lateness: n=18000 p50=0.0869 p90=0.0968 p99=0.1076 p99.9=0.1262 max=0.1997 mean=0.0857
loadgen: [{'vm': 'rv-pbf-lg-4', 'busy_max': 5.7, 'busy_mean': 5.3, 'late_max_us': 532, 'conn_opens': 315, 'max_inflight': 306}]  provider_cpu_busy_max: 18.88139043197915
gateway cores total 2.18 cpu-ms/req {'gateway': 36.414, 'workers': 32.174, 'owners': 4.224}
  rv-pbf-unit-6: cores {'launcher': 0.0, 'owner': 0.084, 'owner0': 0.084, 'redis': 0.001, 'worker': 0.645} worker util max 0.119 per-core max 0.107 mean 0.097 gpu {'0': {'n': 298, 'sm_mean': 5.7, 'sm_p95': 12.0, 'sm_max': 17.0}} t_input_p99 13.0417 per-worker admitted {'n': 6, 'min': 741, 'max': 1138, 'mean': 999.5, 'max_over_mean': 1.139, 'sheds_per_worker': [0, 0, 0, 0, 0, 0]}
  rv-pbf-unit-8: cores {'launcher': 0.0, 'owner': 0.085, 'owner0': 0.085, 'redis': 0.001, 'worker': 0.646} worker util max 0.138 per-core max 0.104 mean 0.094 gpu {'0': {'n': 298, 'sm_mean': 6.1, 'sm_p95': 11.0, 'sm_max': 16.0}} t_input_p99 13.0417 per-worker admitted {'n': 6, 'min': 697, 'max': 1350, 'mean': 999.3, 'max_over_mean': 1.351, 'sheds_per_worker': [0, 0, 0, 0, 0, 1]}
  rv-pbf-unit-9: cores {'launcher': 0.0, 'owner': 0.084, 'owner0': 0.084, 'redis': 0.001, 'worker': 0.633} worker util max 0.135 per-core max 0.101 mean 0.092 gpu {'0': {'n': 298, 'sm_mean': 6.3, 'sm_p95': 13.0, 'sm_max': 18.0}} t_input_p99 13.0417 per-worker admitted {'n': 6, 'min': 731, 'max': 1324, 'mean': 998.2, 'max_over_mean': 1.326, 'sheds_per_worker': [0, 0, 0, 0, 0, 0]}
W t_input_ns: {'n': 17982, 'mean_ms': 7.7197, 'p50_ms': 8.4541, 'p90_ms': 10.4202, 'p99_ms': 13.0417, 'p99.9_ms': 16.9083, 'max_cum_ms': 27.6245}
W t_tokenize_ns: {'n': 17982, 'mean_ms': 2.7889, 'p50_ms': 2.8017, 'p90_ms': 4.2926, 'p99_ms': 4.948, 'p99.9_ms': 6.1276, 'max_cum_ms': 7.4456}
W t_guard_wait_ns: {'n': 17982, 'mean_ms': 4.3529, 'p50_ms': 4.8824, 'p90_ms': 5.4067, 'p99_ms': 7.6349, 'p99.9_ms': 11.862, 'max_cum_ms': 16.0843}
W guard_owner_rtt_ns: {'n': 17982, 'mean_ms': 4.5327, 'p50_ms': 5.079, 'p90_ms': 5.6689, 'p99_ms': 7.8316, 'p99.9_ms': 11.2067, 'max_cum_ms': 16.7258}
W guard_queue_ns: {'n': 17982, 'mean_ms': 0.1207, 'p50_ms': 0.1172, 'p90_ms': 0.1464, 'p99_ms': 0.1812, 'p99.9_ms': 0.8233, 'max_cum_ms': 4.6197}
W guard_exec_ns: {'n': 17982, 'mean_ms': 3.6698, 'p50_ms': 4.2926, 'p90_ms': 4.6203, 'p99_ms': 6.7174, 'p99.9_ms': 6.914, 'max_cum_ms': 9.3175}
W t_admit_ns: {'n': 17982, 'mean_ms': 0.104, 'p50_ms': 0.0906, 'p90_ms': 0.1121, 'p99_ms': 0.8561, 'p99.9_ms': 1.0199, 'max_cum_ms': 6.6699}
W release_processing_ns: {'n': 2760511, 'mean_ms': 0.0651, 'p50_ms': 0.0648, 'p90_ms': 0.0824, 'p99_ms': 0.108, 'p99.9_ms': 0.1423, 'max_cum_ms': 1.5571}
W loop_lag_ns: {'n': 53640, 'mean_ms': 0.7276, 'p50_ms': 0.0084, 'p90_ms': 2.1463, 'p99_ms': 7.2417, 'p99.9_ms': 9.5027, 'max_cum_ms': 20.9809}
W audit_batch_write_ns: {'n': 35683, 'mean_ms': 0.9398, 'p50_ms': 0.8888, 'p90_ms': 1.1059, 'p99_ms': 1.5155, 'p99.9_ms': 7.766, 'max_cum_ms': 15.9328}
W counts: {"admitted": 17982, "audit_enqueued": 35696, "audit_written": 35696, "background_round_trips": 10728, "disposition_ALLOW": 17672, "disposition_BLOCK": 310, "guard_windows": 29590, "lease_refills": 246, "provider_calls": 17672, "provider_connections_opened": 2410, "requests_by_round_trips{n=\"0\"}": 17736, "requests_by_round_trips{n=\"1\"}": 246, "shared_state_round_trips": 246, "shed{reason=\"guard_queue\"}": 1}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 0.075}, "per_proc_util": {"nginx": [0.0, 0.007, 0.008, 0.008, 0.009, 0.01, 0.011, 0.011, 0.011]}, "per_core_util": {"max": 0.012, "mean": 0.01, "sum": 0.08, "n": 8}, "nic": null, "restarted": [], "loadavg_max": 0.15} nginx cpu-ms/req 1.258
redis: ops/s 341.3 ops/req 5.688 cpu cores 0.003 clients 91 mem 401.4MB ping(us) {'n': 28482, 'p50_us': 436.9, 'p90_us': 461.6, 'p99_us': 500.9, 'p99.9_us': 1228.3, 'max_us': 3187.3, 'mean_us': 440.9}
redis cmdstats: {'get': {'calls_per_s': 0.8, 'usec_per_call': 0.64}, 'info': {'calls_per_s': 0.0, 'usec_per_call': 138.0}, 'xadd': {'calls_per_s': 118.9, 'usec_per_call': 2.63}, 'hgetall': {'calls_per_s': 89.4, 'usec_per_call': 0.22}, 'ping': {'calls_per_s': 94.8, 'usec_per_call': 0.1}, 'decrby': {'calls_per_s': 0.8, 'usec_per_call': 0.37}, 'mget': {'calls_per_s': 35.7, 'usec_per_call': 0.38}, 'evalsha': {'calls_per_s': 0.8, 'usec_per_call': 11.61}}
wire: {'requests_in_window': 18000, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56348.5, 'client_side_to_unit': 9411.5, 'unit_to_provider': 10155.4, 'provider_to_unit': 57036.6, 'unit_to_redis': 5675.4, 'redis_to_unit': 425.4}, 'edge_ip_bytes_per_req': {'edge_to_clients': 56478.8, 'clients_to_edge': 8091.8}, 'olg_resp_body_bytes_mean': {'sse': 67550.5, 'json': 1421.2}}
