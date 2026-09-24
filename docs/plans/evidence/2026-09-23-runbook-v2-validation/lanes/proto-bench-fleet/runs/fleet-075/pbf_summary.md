# fleet-075: strict FAIL | load-knee PASS (sut, units=3, rate=75)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 22500 (75.0/s) qualified 22116 (73.72/s) FP-blocks 379 (0.01684) infra 5 (0.00022222222222222223) drops 0 safety 0
infra reasons: {'http_503': 4, 'incomplete': 4, 'unjoined': 4, 'disposition_missing': 4, 'stage_canon_missing': 4, 'stage_det_missing': 4, 'stage_sem_missing': 4, 'stage_resolve_missing': 4, 'stage_dispatch_missing': 4, 'stage_out_missing': 4, 'stage_audit_missing': 4, 'block_on_unavailable_sem': 1}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 4, '403 http_403 type=policy_violation code=blocked_by_policy': 1}

T_fw_addon: n=22116 p50=10.8276 p90=15.9622 p99=53.7463 p99.9=71.4245 max=87.7317 mean=13.1321
T_fw_addon_nohold: n=22116 p50=10.53 p90=13.6184 p99=16.475 p99.9=20.9623 max=36.6762 mean=10.0453
T_fw_addon_sse: n=15486 p50=10.9379 p90=32.2579 p99=54.914 p99.9=71.9895 max=87.7317 mean=14.4215
T_fw_addon_json: n=6630 p50=10.579 p90=13.8075 p99=16.5543 p99.9=20.4935 max=22.7518 mean=10.1205
T_addon_first_sse: n=15486 p50=10.4567 p90=13.9401 p99=30.1399 p99.9=34.856 max=50.9538 mean=10.2811
T_addon_total_sse: n=15486 p50=10.5102 p90=13.5189 p99=16.431 p99.9=20.9722 max=36.6762 mean=10.0131
T_addon_total_json: n=6630 p50=10.579 p90=13.8075 p99=16.5543 p99.9=20.4935 max=22.7518 mean=10.1205
T_release_lag_max: n=1560 p50=50.1023 p90=54.8092 p99=71.9895 p99.9=80.8341 max=87.7317 mean=49.6901
lateness: n=22500 p50=0.0856 p90=0.0955 p99=0.1057 p99.9=0.1252 max=0.2926 mean=0.0858
loadgen: [{'vm': 'rv-pbf-lg-4', 'busy_max': 6.4, 'busy_mean': 5.9, 'late_max_us': 640, 'conn_opens': 391, 'max_inflight': 377}]  provider_cpu_busy_max: 10.911803897959604
gateway cores total 2.66 cpu-ms/req {'gateway': 35.562, 'workers': 31.392, 'owners': 4.157}
  rv-pbf-unit-6: cores {'launcher': 0.0, 'owner': 0.103, 'owner0': 0.103, 'redis': 0.001, 'worker': 0.784} worker util max 0.151 per-core max 0.123 mean 0.115 gpu {'0': {'n': 298, 'sm_mean': 7.6, 'sm_p95': 14.0, 'sm_max': 19.0}} t_input_p99 13.0417 per-worker admitted {'n': 6, 'min': 1020, 'max': 1519, 'mean': 1247.7, 'max_over_mean': 1.217, 'sheds_per_worker': [0, 0, 0, 0, 1, 1]}
  rv-pbf-unit-8: cores {'launcher': 0.0, 'owner': 0.103, 'owner0': 0.103, 'redis': 0.001, 'worker': 0.789} worker util max 0.159 per-core max 0.122 mean 0.114 gpu {'0': {'n': 298, 'sm_mean': 7.6, 'sm_p95': 13.0, 'sm_max': 18.0}} t_input_p99 13.1727 per-worker admitted {'n': 6, 'min': 1117, 'max': 1567, 'mean': 1249.0, 'max_over_mean': 1.255, 'sheds_per_worker': [0, 0, 0, 0, 0, 0]}
  rv-pbf-unit-9: cores {'launcher': 0.0, 'owner': 0.105, 'owner0': 0.105, 'redis': 0.001, 'worker': 0.774} worker util max 0.155 per-core max 0.118 mean 0.113 gpu {'0': {'n': 298, 'sm_mean': 7.7, 'sm_p95': 15.0, 'sm_max': 20.0}} t_input_p99 13.1727 per-worker admitted {'n': 6, 'min': 897, 'max': 1483, 'mean': 1249.3, 'max_over_mean': 1.187, 'sheds_per_worker': [0, 0, 0, 0, 1, 1]}
W t_input_ns: {'n': 22471, 'mean_ms': 7.7843, 'p50_ms': 8.4541, 'p90_ms': 10.5513, 'p99_ms': 13.0417, 'p99.9_ms': 16.5806, 'max_cum_ms': 38.1453}
W t_tokenize_ns: {'n': 22476, 'mean_ms': 2.9102, 'p50_ms': 2.9327, 'p90_ms': 4.4892, 'p99_ms': 5.1446, 'p99.9_ms': 6.2587, 'max_cum_ms': 7.4456}
W t_guard_wait_ns: {'n': 22471, 'mean_ms': 4.2974, 'p50_ms': 4.8824, 'p90_ms': 5.4067, 'p99_ms': 7.5039, 'p99.9_ms': 11.4688, 'max_cum_ms': 16.0843}
W guard_owner_rtt_ns: {'n': 22471, 'mean_ms': 4.4795, 'p50_ms': 5.079, 'p90_ms': 5.6033, 'p99_ms': 7.7005, 'p99.9_ms': 10.6824, 'max_cum_ms': 32.7484}
W guard_queue_ns: {'n': 22470, 'mean_ms': 0.1176, 'p50_ms': 0.1142, 'p90_ms': 0.1423, 'p99_ms': 0.1731, 'p99.9_ms': 0.2509, 'max_cum_ms': 4.6197}
W guard_exec_ns: {'n': 22470, 'mean_ms': 3.6326, 'p50_ms': 4.2926, 'p90_ms': 4.6203, 'p99_ms': 6.6519, 'p99.9_ms': 6.8485, 'max_cum_ms': 9.3175}
W t_admit_ns: {'n': 22476, 'mean_ms': 0.1032, 'p50_ms': 0.0896, 'p90_ms': 0.1121, 'p99_ms': 0.8643, 'p99.9_ms': 1.0568, 'max_cum_ms': 6.6699}
W release_processing_ns: {'n': 3436043, 'mean_ms': 0.0643, 'p50_ms': 0.0637, 'p90_ms': 0.0824, 'p99_ms': 0.108, 'p99.9_ms': 0.1423, 'max_cum_ms': 1.5571}
W loop_lag_ns: {'n': 53610, 'mean_ms': 0.7535, 'p50_ms': 0.0099, 'p90_ms': 2.5068, 'p99_ms': 7.5694, 'p99.9_ms': 9.8959, 'max_cum_ms': 26.0693}
W audit_batch_write_ns: {'n': 44534, 'mean_ms': 0.9403, 'p50_ms': 0.8888, 'p90_ms': 1.1059, 'p99_ms': 1.5155, 'p99.9_ms': 8.0282, 'max_cum_ms': 24.1796}
W counts: {"admitted": 22476, "audit_enqueued": 44557, "audit_written": 44557, "background_round_trips": 10722, "disposition_ALLOW": 22091, "disposition_BLOCK": 380, "guard_deadline_expired": 1, "guard_unavailable_findings": 1, "guard_windows": 36909, "lease_refills": 313, "provider_calls": 22091, "provider_connections_opened": 3326, "requests_by_round_trips{n=\"0\"}": 22163, "requests_by_round_trips{n=\"1\"}": 313, "shared_state_round_trips": 313, "shed{reason=\"guard_queue\"}": 4}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 0.099}, "per_proc_util": {"nginx": [0.0, 0.009, 0.01, 0.012, 0.012, 0.012, 0.013, 0.015, 0.015]}, "per_core_util": {"max": 0.015, "mean": 0.013, "sum": 0.11, "n": 8}, "nic": null, "restarted": [], "loadavg_max": 0.23} nginx cpu-ms/req 1.327
redis: ops/s 371.0 ops/req 4.946 cpu cores 0.004 clients 91 mem 554.2MB ping(us) {'n': 28312, 'p50_us': 500.0, 'p90_us': 524.6, 'p99_us': 561.1, 'p99.9_us': 731.5, 'max_us': 2862.4, 'mean_us': 503.4}
redis cmdstats: {'get': {'calls_per_s': 1.0, 'usec_per_call': 0.58}, 'info': {'calls_per_s': 0.0, 'usec_per_call': 140.0}, 'xadd': {'calls_per_s': 148.5, 'usec_per_call': 2.65}, 'hgetall': {'calls_per_s': 89.4, 'usec_per_call': 0.22}, 'ping': {'calls_per_s': 94.2, 'usec_per_call': 0.1}, 'decrby': {'calls_per_s': 1.0, 'usec_per_call': 0.45}, 'mget': {'calls_per_s': 35.7, 'usec_per_call': 0.4}, 'evalsha': {'calls_per_s': 1.0, 'usec_per_call': 11.04}}
wire: {'requests_in_window': 22500, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56167.6, 'client_side_to_unit': 9346.8, 'unit_to_provider': 10088.2, 'provider_to_unit': 56860.8, 'unit_to_redis': 5610.8, 'redis_to_unit': 389.7}, 'edge_ip_bytes_per_req': {'edge_to_clients': 56282.7, 'clients_to_edge': 7979.0}, 'olg_resp_body_bytes_mean': {'sse': 67504.3, 'json': 1419.4}}
