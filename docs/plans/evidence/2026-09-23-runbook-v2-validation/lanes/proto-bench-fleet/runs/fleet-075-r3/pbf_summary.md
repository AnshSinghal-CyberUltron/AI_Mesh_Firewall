# fleet-075-r3: strict FAIL | load-knee PASS (sut, units=3, rate=75)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 22500 (75.0/s) qualified 22099 (73.66/s) FP-blocks 394 (0.01751) infra 7 (0.0003111111111111111) drops 0 safety 0
infra reasons: {'http_503': 7, 'incomplete': 7, 'unjoined': 7, 'disposition_missing': 7, 'stage_canon_missing': 7, 'stage_det_missing': 7, 'stage_sem_missing': 7, 'stage_resolve_missing': 7, 'stage_dispatch_missing': 7, 'stage_out_missing': 7, 'stage_audit_missing': 7}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 7}

T_fw_addon: n=22099 p50=11.2803 p90=16.6276 p99=54.4809 p99.9=71.8975 max=75.8789 mean=13.7034
T_fw_addon_nohold: n=22099 p50=10.9333 p90=14.2376 p99=17.3624 p99.9=21.8802 max=25.5117 mean=10.5408
T_fw_addon_sse: n=15471 p50=11.4008 p90=36.0764 p99=55.1999 p99.9=72.5159 max=75.8789 mean=15.0137
T_fw_addon_json: n=6628 p50=11.0089 p90=14.3972 p99=17.4926 p99.9=22.2962 max=25.5117 mean=10.6451
T_addon_first_sse: n=15471 p50=10.8483 p90=14.4229 p99=28.4329 p99.9=34.9546 max=52.8375 mean=10.7148
T_addon_total_sse: n=15471 p50=10.8993 p90=14.1457 p99=17.1837 p99.9=21.8802 max=24.5824 mean=10.4962
T_addon_total_json: n=6628 p50=11.0089 p90=14.3972 p99=17.4926 p99.9=22.2962 max=25.5117 mean=10.6451
T_release_lag_max: n=1619 p50=50.6869 p90=55.1263 p99=72.4423 p99.9=74.7261 max=75.8789 mean=50.1981
lateness: n=22500 p50=0.085 p90=0.0935 p99=0.1032 p99.9=0.1252 max=0.2982 mean=0.0851
loadgen: [{'vm': 'rv-pbf-lg-4', 'busy_max': 19.2, 'busy_mean': 6.1, 'late_max_us': 605, 'conn_opens': 390, 'max_inflight': 377}]  provider_cpu_busy_max: 20.42918652412714
gateway cores total 2.68 cpu-ms/req {'gateway': 35.818, 'workers': 31.611, 'owners': 4.195}
  rv-pbf-unit-6: cores {'launcher': 0.0, 'owner': 0.105, 'owner0': 0.105, 'redis': 0.001, 'worker': 0.793} worker util max 0.173 per-core max 0.125 mean 0.118 gpu {'0': {'n': 298, 'sm_mean': 7.5, 'sm_p95': 13.0, 'sm_max': 19.0}} t_input_p99 13.3038 per-worker admitted {'n': 6, 'min': 781, 'max': 1694, 'mean': 1249.7, 'max_over_mean': 1.356, 'sheds_per_worker': [0, 0, 1, 1, 1, 2]}
  rv-pbf-unit-8: cores {'launcher': 0.0, 'owner': 0.104, 'owner0': 0.104, 'redis': 0.001, 'worker': 0.787} worker util max 0.162 per-core max 0.125 mean 0.117 gpu {'0': {'n': 298, 'sm_mean': 7.6, 'sm_p95': 14.0, 'sm_max': 17.0}} t_input_p99 13.3038 per-worker admitted {'n': 6, 'min': 1052, 'max': 1604, 'mean': 1249.3, 'max_over_mean': 1.284, 'sheds_per_worker': [0, 0, 0, 0, 0, 1]}
  rv-pbf-unit-9: cores {'launcher': 0.0, 'owner': 0.105, 'owner0': 0.105, 'redis': 0.001, 'worker': 0.783} worker util max 0.163 per-core max 0.123 mean 0.116 gpu {'0': {'n': 298, 'sm_mean': 7.9, 'sm_p95': 13.0, 'sm_max': 18.0}} t_input_p99 13.3038 per-worker admitted {'n': 6, 'min': 790, 'max': 1642, 'mean': 1249.3, 'max_over_mean': 1.314, 'sheds_per_worker': [0, 0, 0, 0, 0, 1]}
W t_input_ns: {'n': 22483, 'mean_ms': 7.8508, 'p50_ms': 8.5852, 'p90_ms': 10.6824, 'p99_ms': 13.3038, 'p99.9_ms': 17.1704, 'max_cum_ms': 38.1453}
W t_tokenize_ns: {'n': 22490, 'mean_ms': 2.9239, 'p50_ms': 2.9327, 'p90_ms': 4.4892, 'p99_ms': 5.2101, 'p99.9_ms': 6.3898, 'max_cum_ms': 8.0436}
W t_guard_wait_ns: {'n': 22483, 'mean_ms': 4.3401, 'p50_ms': 4.8824, 'p90_ms': 5.4067, 'p99_ms': 7.5694, 'p99.9_ms': 11.9931, 'max_cum_ms': 22.8276}
W guard_owner_rtt_ns: {'n': 22483, 'mean_ms': 4.5224, 'p50_ms': 5.079, 'p90_ms': 5.6033, 'p99_ms': 7.766, 'p99.9_ms': 11.3377, 'max_cum_ms': 32.7484}
W guard_queue_ns: {'n': 22483, 'mean_ms': 0.1213, 'p50_ms': 0.1183, 'p90_ms': 0.1464, 'p99_ms': 0.1792, 'p99.9_ms': 0.4403, 'max_cum_ms': 4.6197}
W guard_exec_ns: {'n': 22483, 'mean_ms': 3.6548, 'p50_ms': 4.2926, 'p90_ms': 4.6203, 'p99_ms': 6.7174, 'p99.9_ms': 6.8485, 'max_cum_ms': 9.3175}
W t_admit_ns: {'n': 22490, 'mean_ms': 0.1061, 'p50_ms': 0.0906, 'p90_ms': 0.1111, 'p99_ms': 0.8724, 'p99.9_ms': 1.5155, 'max_cum_ms': 6.6699}
W release_processing_ns: {'n': 3435670, 'mean_ms': 0.0647, 'p50_ms': 0.0643, 'p90_ms': 0.0835, 'p99_ms': 0.108, 'p99.9_ms': 0.1403, 'max_cum_ms': 22.0257}
W loop_lag_ns: {'n': 53620, 'mean_ms': 0.7728, 'p50_ms': 0.0093, 'p90_ms': 2.8017, 'p99_ms': 7.8316, 'p99.9_ms': 10.8134, 'max_cum_ms': 26.0693}
W audit_batch_write_ns: {'n': 44536, 'mean_ms': 1.2605, 'p50_ms': 1.3517, 'p90_ms': 1.5811, 'p99_ms': 1.9579, 'p99.9_ms': 8.9784, 'max_cum_ms': 28.6832}
W counts: {"admitted": 22490, "audit_enqueued": 44569, "audit_written": 44569, "background_round_trips": 10724, "disposition_ALLOW": 22089, "disposition_BLOCK": 394, "guard_windows": 36932, "lease_refills": 310, "provider_calls": 22089, "provider_connections_opened": 3215, "requests_by_round_trips{n=\"0\"}": 22180, "requests_by_round_trips{n=\"1\"}": 310, "shared_state_round_trips": 310, "shed{reason=\"guard_queue\"}": 7}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 0.098}, "per_proc_util": {"nginx": [0.0, 0.01, 0.01, 0.012, 0.012, 0.013, 0.013, 0.013, 0.016]}, "per_core_util": {"max": 0.013, "mean": 0.013, "sum": 0.1, "n": 8}, "nic": null, "restarted": [], "loadavg_max": 0.18} nginx cpu-ms/req 1.307
redis: ops/s 370.8 ops/req 4.944 cpu cores 0.004 clients 91 mem 1026.8MB ping(us) {'n': 28304, 'p50_us': 499.4, 'p90_us': 524.4, 'p99_us': 578.0, 'p99.9_us': 1560.7, 'max_us': 4021.9, 'mean_us': 504.5}
redis cmdstats: {'get': {'calls_per_s': 1.0, 'usec_per_call': 0.62}, 'info': {'calls_per_s': 0.0, 'usec_per_call': 132.0}, 'xadd': {'calls_per_s': 148.5, 'usec_per_call': 2.69}, 'hgetall': {'calls_per_s': 89.3, 'usec_per_call': 0.22}, 'ping': {'calls_per_s': 94.2, 'usec_per_call': 0.11}, 'decrby': {'calls_per_s': 1.0, 'usec_per_call': 0.39}, 'mget': {'calls_per_s': 35.7, 'usec_per_call': 0.39}, 'evalsha': {'calls_per_s': 1.0, 'usec_per_call': 11.38}}
wire: {'requests_in_window': 22500, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56135.7, 'client_side_to_unit': 9318.2, 'unit_to_provider': 9982.1, 'provider_to_unit': 56821.2, 'unit_to_redis': 5608.3, 'redis_to_unit': 389.5}, 'edge_ip_bytes_per_req': {'edge_to_clients': 56251.4, 'clients_to_edge': 7994.9}, 'olg_resp_body_bytes_mean': {'sse': 67530.5, 'json': 1419.6}}
