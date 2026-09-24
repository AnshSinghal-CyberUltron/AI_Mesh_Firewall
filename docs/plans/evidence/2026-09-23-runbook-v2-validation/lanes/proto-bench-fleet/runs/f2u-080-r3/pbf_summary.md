# f2u-080-r3: strict FAIL | load-knee PASS (sut, units=2, rate=80)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 24000 (80.0/s) qualified 23607 (78.69/s) FP-blocks 390 (0.01625) infra 3 (0.000125) drops 0 safety 0
infra reasons: {'http_503': 3, 'incomplete': 3, 'unjoined': 3, 'disposition_missing': 3, 'stage_canon_missing': 3, 'stage_det_missing': 3, 'stage_sem_missing': 3, 'stage_resolve_missing': 3, 'stage_dispatch_missing': 3, 'stage_out_missing': 3, 'stage_audit_missing': 3}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 3}

T_fw_addon: n=23607 p50=10.4555 p90=15.1543 p99=53.3815 p99.9=70.8732 max=105.5698 mean=12.7619
T_fw_addon_nohold: n=23607 p50=10.224 p90=13.0768 p99=15.7598 p99.9=19.8528 max=36.2984 mean=9.6702
T_fw_addon_sse: n=16522 p50=10.5235 p90=33.0448 p99=54.0832 p99.9=71.1384 max=105.5698 mean=14.0592
T_fw_addon_json: n=7085 p50=10.307 p90=13.233 p99=15.5802 p99.9=20.1271 max=23.8067 mean=9.7367
T_addon_first_sse: n=16522 p50=10.117 p90=13.4782 p99=29.7696 p99.9=34.2368 max=52.6301 mean=9.8783
T_addon_total_sse: n=16522 p50=10.1888 p90=13.0213 p99=15.9035 p99.9=19.6495 max=36.2984 mean=9.6417
T_addon_total_json: n=7085 p50=10.307 p90=13.233 p99=15.5802 p99.9=20.1271 max=23.8067 mean=9.7367
T_release_lag_max: n=1696 p50=49.8917 p90=54.0542 p99=71.1384 p99.9=92.2394 max=105.5698 mean=49.2365
lateness: n=24000 p50=0.086 p90=0.0954 p99=0.1069 p99.9=0.1259 max=0.2615 mean=0.0863
loadgen: [{'vm': 'rv-pbf-lg-1', 'busy_max': 15.8, 'busy_mean': 4.3, 'late_max_us': 346, 'conn_opens': 151, 'max_inflight': 146}, {'vm': 'rv-pbf-lg-2', 'busy_max': 16.0, 'busy_mean': 4.3, 'late_max_us': 204, 'conn_opens': 150, 'max_inflight': 146}, {'vm': 'rv-pbf-lg-3', 'busy_max': 16.8, 'busy_mean': 4.4, 'late_max_us': 261, 'conn_opens': 152, 'max_inflight': 147}]  provider_cpu_busy_max: 17.907916054523177
gateway cores total 2.95 cpu-ms/req {'gateway': 36.995, 'workers': 32.847, 'owners': 4.137}
  rv-pbf-unit-2: cores {'launcher': 0.0, 'owner': 0.166, 'owner0': 0.08, 'owner1': 0.086, 'redis': 0.001, 'worker': 1.33} worker util max 0.096 per-core max 0.08 mean 0.066 gpu {'0': {'n': 298, 'sm_mean': 5.8, 'sm_p95': 13.0, 'sm_max': 20.0}, '1': {'n': 298, 'sm_mean': 6.0, 'sm_p95': 13.0, 'sm_max': 18.0}} t_input_p99 12.7795 per-worker admitted {'n': 18, 'min': 299, 'max': 912, 'mean': 666.6, 'max_over_mean': 1.368, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1]}
  rv-pbf-unit-3: cores {'launcher': 0.0, 'owner': 0.163, 'owner0': 0.077, 'owner1': 0.086, 'redis': 0.001, 'worker': 1.289} worker util max 0.095 per-core max 0.077 mean 0.064 gpu {'0': {'n': 298, 'sm_mean': 5.8, 'sm_p95': 13.0, 'sm_max': 24.0}, '1': {'n': 298, 'sm_mean': 6.5, 'sm_p95': 15.0, 'sm_max': 20.0}} t_input_p99 12.5174 per-worker admitted {'n': 18, 'min': 324, 'max': 936, 'mean': 666.1, 'max_over_mean': 1.405, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1]}
W t_input_ns: {'n': 23985, 'mean_ms': 7.5296, 'p50_ms': 8.2248, 'p90_ms': 10.1581, 'p99_ms': 12.6484, 'p99.9_ms': 16.0563, 'max_cum_ms': 36.429}
W t_tokenize_ns: {'n': 23988, 'mean_ms': 2.7429, 'p50_ms': 2.7689, 'p90_ms': 4.2271, 'p99_ms': 4.8169, 'p99.9_ms': 6.1276, 'max_cum_ms': 8.9355}
W t_guard_wait_ns: {'n': 23985, 'mean_ms': 4.246, 'p50_ms': 4.7514, 'p90_ms': 5.2756, 'p99_ms': 7.3728, 'p99.9_ms': 11.862, 'max_cum_ms': 29.7032}
W guard_owner_rtt_ns: {'n': 23985, 'mean_ms': 4.417, 'p50_ms': 4.948, 'p90_ms': 5.4723, 'p99_ms': 7.6349, 'p99.9_ms': 11.0756, 'max_cum_ms': 28.7454}
W guard_queue_ns: {'n': 23985, 'mean_ms': 0.1105, 'p50_ms': 0.1091, 'p90_ms': 0.1306, 'p99_ms': 0.1567, 'p99.9_ms': 0.1997, 'max_cum_ms': 16.1001}
W guard_exec_ns: {'n': 23985, 'mean_ms': 3.618, 'p50_ms': 4.2271, 'p90_ms': 4.5548, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 8.1548}
W t_admit_ns: {'n': 23988, 'mean_ms': 0.0947, 'p50_ms': 0.0886, 'p90_ms': 0.105, 'p99_ms': 0.1306, 'p99.9_ms': 1.0199, 'max_cum_ms': 21.7487}
W release_processing_ns: {'n': 3665222, 'mean_ms': 0.0637, 'p50_ms': 0.0637, 'p90_ms': 0.0794, 'p99_ms': 0.1019, 'p99.9_ms': 0.1306, 'max_cum_ms': 26.8838}
W loop_lag_ns: {'n': 107260, 'mean_ms': 0.7398, 'p50_ms': 0.0062, 'p90_ms': 1.7449, 'p99_ms': 7.3728, 'p99.9_ms': 9.2406, 'max_cum_ms': 27.5291}
W audit_batch_write_ns: {'n': 47561, 'mean_ms': 0.9962, 'p50_ms': 0.938, 'p90_ms': 1.1715, 'p99_ms': 1.4828, 'p99.9_ms': 7.9626, 'max_cum_ms': 40.9418}
W counts: {"admitted": 23988, "audit_enqueued": 47573, "audit_written": 47573, "background_round_trips": 21452, "disposition_ALLOW": 23595, "disposition_BLOCK": 390, "guard_windows": 39595, "lease_refills": 113, "provider_calls": 23595, "provider_connections_opened": 2847, "requests_by_round_trips{n=\"0\"}": 23875, "requests_by_round_trips{n=\"1\"}": 113, "shared_state_round_trips": 113, "shed{reason=\"guard_queue\"}": 3}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 0.091}, "per_proc_util": {"nginx": [0.0, 0.004, 0.004, 0.004, 0.004, 0.005, 0.005, 0.006, 0.006, 0.006, 0.006, 0.006, 0.006, 0.007, 0.007, 0.008, 0.008]}, "per_core_util": {"max": 0.009, "mean": 0.006, "sum": 0.1, "n": 16}, "nic": null, "restarted": [], "loadavg_max": 0.28} nginx cpu-ms/req 1.142
redis: ops/s 755.0 ops/req 9.438 cpu cores 0.006 clients 291 mem 1369.0MB ping(us) {'n': 28123, 'p50_us': 553.4, 'p90_us': 629.0, 'p99_us': 700.5, 'p99.9_us': 2868.5, 'max_us': 5019.2, 'mean_us': 571.0}
redis cmdstats: {'info': {'calls_per_s': 0.0, 'usec_per_call': 134.0}, 'ping': {'calls_per_s': 93.6, 'usec_per_call': 0.1}, 'evalsha': {'calls_per_s': 0.4, 'usec_per_call': 11.56}, 'xadd': {'calls_per_s': 158.5, 'usec_per_call': 3.68}, 'mget': {'calls_per_s': 143.4, 'usec_per_call': 0.38}, 'hgetall': {'calls_per_s': 358.4, 'usec_per_call': 0.21}, 'get': {'calls_per_s': 0.4, 'usec_per_call': 0.63}, 'decrby': {'calls_per_s': 0.4, 'usec_per_call': 0.46}}
wire: {'requests_in_window': 24000, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56140.9, 'client_side_to_unit': 9063.0, 'unit_to_provider': 9853.2, 'provider_to_unit': 56821.3, 'unit_to_redis': 5744.6, 'redis_to_unit': 442.3}, 'edge_ip_bytes_per_req': {'edge_to_clients': 56260.3, 'clients_to_edge': 8346.3}, 'olg_resp_body_bytes_mean': {'sse': 67319.7, 'json': 1433.3}}
