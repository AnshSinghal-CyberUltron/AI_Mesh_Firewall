# fleet-075-r2: strict FAIL | load-knee PASS (sut, units=3, rate=75)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 22500 (75.0/s) qualified 22092 (73.64/s) FP-blocks 400 (0.01778) infra 8 (0.00035555555555555557) drops 0 safety 0
infra reasons: {'http_503': 8, 'incomplete': 8, 'unjoined': 8, 'disposition_missing': 8, 'stage_canon_missing': 8, 'stage_det_missing': 8, 'stage_sem_missing': 8, 'stage_resolve_missing': 8, 'stage_dispatch_missing': 8, 'stage_out_missing': 8, 'stage_audit_missing': 8}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 8}

T_fw_addon: n=22092 p50=10.9026 p90=16.0789 p99=53.8872 p99.9=71.6798 max=91.5666 mean=13.1687
T_fw_addon_nohold: n=22092 p50=10.6157 p90=13.669 p99=16.6907 p99.9=21.2393 max=26.3237 mean=10.1091
T_fw_addon_sse: n=15463 p50=11.0085 p90=31.9319 p99=54.8143 p99.9=72.1575 max=91.5666 mean=14.4508
T_fw_addon_json: n=6629 p50=10.661 p90=13.8277 p99=16.5786 p99.9=21.5782 max=24.7724 mean=10.1781
T_addon_first_sse: n=15463 p50=10.5371 p90=14.0272 p99=29.4955 p99.9=34.0776 max=51.6421 mean=10.3236
T_addon_total_sse: n=15463 p50=10.5997 p90=13.5954 p99=16.7132 p99.9=21.11 max=26.3237 mean=10.0795
T_addon_total_json: n=6629 p50=10.661 p90=13.8277 p99=16.5786 p99.9=21.5782 max=24.7724 mean=10.1781
T_release_lag_max: n=1548 p50=50.3933 p90=54.8143 p99=72.1575 p99.9=77.5195 max=91.5666 mean=49.8605
lateness: n=22500 p50=0.0847 p90=0.0956 p99=0.1054 p99.9=0.1243 max=0.3319 mean=0.0845
loadgen: [{'vm': 'rv-pbf-lg-4', 'busy_max': 8.3, 'busy_mean': 6.0, 'late_max_us': 599, 'conn_opens': 391, 'max_inflight': 377}]  provider_cpu_busy_max: 17.61743170433031
gateway cores total 2.67 cpu-ms/req {'gateway': 35.699, 'workers': 31.497, 'owners': 4.19}
  rv-pbf-unit-6: cores {'launcher': 0.0, 'owner': 0.105, 'owner0': 0.105, 'redis': 0.001, 'worker': 0.792} worker util max 0.167 per-core max 0.124 mean 0.116 gpu {'0': {'n': 298, 'sm_mean': 7.7, 'sm_p95': 12.0, 'sm_max': 19.0}} t_input_p99 13.3038 per-worker admitted {'n': 6, 'min': 1106, 'max': 1594, 'mean': 1249.2, 'max_over_mean': 1.276, 'sheds_per_worker': [0, 0, 0, 1, 1, 1]}
  rv-pbf-unit-8: cores {'launcher': 0.0, 'owner': 0.104, 'owner0': 0.104, 'redis': 0.001, 'worker': 0.784} worker util max 0.171 per-core max 0.125 mean 0.115 gpu {'0': {'n': 298, 'sm_mean': 7.4, 'sm_p95': 13.0, 'sm_max': 20.0}} t_input_p99 13.0417 per-worker admitted {'n': 6, 'min': 733, 'max': 1732, 'mean': 1250.2, 'max_over_mean': 1.385, 'sheds_per_worker': [0, 0, 0, 0, 1, 1]}
  rv-pbf-unit-9: cores {'launcher': 0.0, 'owner': 0.104, 'owner0': 0.104, 'redis': 0.001, 'worker': 0.778} worker util max 0.163 per-core max 0.119 mean 0.114 gpu {'0': {'n': 298, 'sm_mean': 7.7, 'sm_p95': 14.0, 'sm_max': 18.0}} t_input_p99 13.3038 per-worker admitted {'n': 6, 'min': 927, 'max': 1631, 'mean': 1248.2, 'max_over_mean': 1.307, 'sheds_per_worker': [0, 0, 0, 1, 1, 1]}
W t_input_ns: {'n': 22477, 'mean_ms': 7.8372, 'p50_ms': 8.5852, 'p90_ms': 10.5513, 'p99_ms': 13.3038, 'p99.9_ms': 17.4326, 'max_cum_ms': 38.1453}
W t_tokenize_ns: {'n': 22485, 'mean_ms': 2.9222, 'p50_ms': 2.9655, 'p90_ms': 4.4892, 'p99_ms': 5.1446, 'p99.9_ms': 6.0621, 'max_cum_ms': 7.4456}
W t_guard_wait_ns: {'n': 22477, 'mean_ms': 4.3333, 'p50_ms': 4.8824, 'p90_ms': 5.4067, 'p99_ms': 7.5039, 'p99.9_ms': 11.9931, 'max_cum_ms': 22.8276}
W guard_owner_rtt_ns: {'n': 22477, 'mean_ms': 4.5149, 'p50_ms': 5.079, 'p90_ms': 5.6033, 'p99_ms': 7.766, 'p99.9_ms': 11.3377, 'max_cum_ms': 32.7484}
W guard_queue_ns: {'n': 22477, 'mean_ms': 0.1208, 'p50_ms': 0.1172, 'p90_ms': 0.1444, 'p99_ms': 0.1772, 'p99.9_ms': 0.8643, 'max_cum_ms': 4.6197}
W guard_exec_ns: {'n': 22477, 'mean_ms': 3.6526, 'p50_ms': 4.2926, 'p90_ms': 4.6203, 'p99_ms': 6.7174, 'p99.9_ms': 6.8485, 'max_cum_ms': 9.3175}
W t_admit_ns: {'n': 22485, 'mean_ms': 0.1038, 'p50_ms': 0.0906, 'p90_ms': 0.1121, 'p99_ms': 0.8479, 'p99.9_ms': 1.0568, 'max_cum_ms': 6.6699}
W release_processing_ns: {'n': 3434670, 'mean_ms': 0.0644, 'p50_ms': 0.0637, 'p90_ms': 0.0824, 'p99_ms': 0.108, 'p99.9_ms': 0.1403, 'max_cum_ms': 22.0257}
W loop_lag_ns: {'n': 53620, 'mean_ms': 0.7668, 'p50_ms': 0.0103, 'p90_ms': 2.5395, 'p99_ms': 7.7005, 'p99.9_ms': 9.6338, 'max_cum_ms': 26.0693}
W audit_batch_write_ns: {'n': 44528, 'mean_ms': 0.9416, 'p50_ms': 0.8888, 'p90_ms': 1.1059, 'p99_ms': 1.5811, 'p99.9_ms': 8.3558, 'max_cum_ms': 28.6832}
W counts: {"admitted": 22485, "audit_enqueued": 44558, "audit_written": 44558, "background_round_trips": 10724, "disposition_ALLOW": 22077, "disposition_BLOCK": 400, "guard_windows": 36922, "lease_refills": 309, "provider_calls": 22077, "provider_connections_opened": 3283, "requests_by_round_trips{n=\"0\"}": 22176, "requests_by_round_trips{n=\"1\"}": 309, "shared_state_round_trips": 309, "shed{reason=\"guard_queue\"}": 8}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 0.099}, "per_proc_util": {"nginx": [0.0, 0.011, 0.011, 0.012, 0.012, 0.012, 0.012, 0.013, 0.015]}, "per_core_util": {"max": 0.013, "mean": 0.013, "sum": 0.1, "n": 8}, "nic": null, "restarted": [], "loadavg_max": 0.35} nginx cpu-ms/req 1.32
redis: ops/s 371.5 ops/req 4.953 cpu cores 0.004 clients 91 mem 873.9MB ping(us) {'n': 28490, 'p50_us': 430.8, 'p90_us': 455.4, 'p99_us': 499.2, 'p99.9_us': 1387.5, 'max_us': 3829.8, 'mean_us': 435.5}
redis cmdstats: {'get': {'calls_per_s': 1.0, 'usec_per_call': 0.55}, 'info': {'calls_per_s': 0.0, 'usec_per_call': 139.0}, 'xadd': {'calls_per_s': 148.4, 'usec_per_call': 2.63}, 'hgetall': {'calls_per_s': 89.4, 'usec_per_call': 0.23}, 'ping': {'calls_per_s': 94.8, 'usec_per_call': 0.1}, 'decrby': {'calls_per_s': 1.0, 'usec_per_call': 0.44}, 'mget': {'calls_per_s': 35.7, 'usec_per_call': 0.38}, 'evalsha': {'calls_per_s': 1.0, 'usec_per_call': 10.97}}
wire: {'requests_in_window': 22500, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56113.5, 'client_side_to_unit': 9302.1, 'unit_to_provider': 10037.1, 'provider_to_unit': 56798.3, 'unit_to_redis': 5607.7, 'redis_to_unit': 389.9}, 'edge_ip_bytes_per_req': {'edge_to_clients': 56229.5, 'clients_to_edge': 7996.3}, 'olg_resp_body_bytes_mean': {'sse': 67537.7, 'json': 1419.4}}
