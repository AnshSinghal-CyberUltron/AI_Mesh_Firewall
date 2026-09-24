# x1u-100: strict FAIL | load-knee FAIL (sut, units=1, rate=100)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 6000 (100.0/s) qualified 5897 (98.28/s) FP-blocks 57 (0.0095) infra 46 (0.007666666666666666) drops 0 safety 0
infra reasons: {'http_503': 46, 'incomplete': 46, 'unjoined': 46, 'disposition_missing': 46, 'stage_canon_missing': 46, 'stage_det_missing': 46, 'stage_sem_missing': 46, 'stage_resolve_missing': 46, 'stage_dispatch_missing': 46, 'stage_out_missing': 46, 'stage_audit_missing': 46}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 46}

T_fw_addon: n=5897 p50=10.8487 p90=16.7653 p99=54.5197 p99.9=72.1116 max=75.3724 mean=13.4333
T_fw_addon_nohold: n=5897 p50=10.6016 p90=14.0125 p99=17.7102 p99.9=21.8556 max=25.4315 mean=10.206
T_fw_addon_sse: n=4124 p50=10.9978 p90=37.7608 p99=56.0994 p99.9=72.3319 max=75.3724 mean=14.8117
T_fw_addon_json: n=1773 p50=10.6175 p90=14.2848 p99=17.6937 p99.9=22.0207 max=22.0881 mean=10.2273
T_addon_first_sse: n=4124 p50=10.5103 p90=14.1647 p99=28.2599 p99.9=35.4546 max=51.7077 mean=10.4122
T_addon_total_sse: n=4124 p50=10.5914 p90=13.9153 p99=17.7399 p99.9=20.9356 max=25.4315 mean=10.1968
T_addon_total_json: n=1773 p50=10.6175 p90=14.2848 p99=17.6937 p99.9=22.0207 max=22.0881 mean=10.2273
T_release_lag_max: n=432 p50=50.5193 p90=55.7511 p99=72.3319 p99.9=75.3724 max=75.3724 mean=50.697
lateness: n=6000 p50=0.0871 p90=0.0977 p99=0.1083 p99.9=0.1204 max=0.5935 mean=0.0875
loadgen: [{'vm': 'rv-pbf-lg-1', 'busy_max': 4.8, 'busy_mean': 4.6, 'late_max_us': 284, 'conn_opens': 170, 'max_inflight': 170}, {'vm': 'rv-pbf-lg-2', 'busy_max': 4.6, 'busy_mean': 4.3, 'late_max_us': 153, 'conn_opens': 169, 'max_inflight': 169}, {'vm': 'rv-pbf-lg-3', 'busy_max': 4.7, 'busy_mean': 4.5, 'late_max_us': 593, 'conn_opens': 170, 'max_inflight': 170}]  provider_cpu_busy_max: 13.514427531929861
gateway cores total 3.5 cpu-ms/req {'gateway': 35.55, 'workers': 31.513, 'owners': 4.033}
  rv-pbf-unit-2: cores {'launcher': 0.0, 'owner': 0.397, 'owner0': 0.199, 'owner1': 0.197, 'redis': 0.001, 'worker': 3.1} worker util max 0.24 per-core max 0.17 mean 0.149 gpu {'0': {'n': 61, 'sm_mean': 15.6, 'sm_p95': 26.0, 'sm_max': 28.0}, '1': {'n': 61, 'sm_mean': 14.9, 'sm_p95': 26.0, 'sm_max': 33.0}} t_input_p99 14.4835
W t_input_ns: {'n': 5939, 'mean_ms': 7.809, 'p50_ms': 8.4541, 'p90_ms': 10.6824, 'p99_ms': 14.4835, 'p99.9_ms': 18.7433, 'max_cum_ms': 35.9043}
W t_tokenize_ns: {'n': 5983, 'mean_ms': 2.9505, 'p50_ms': 3.031, 'p90_ms': 4.4892, 'p99_ms': 5.2756, 'p99.9_ms': 6.5208, 'max_cum_ms': 8.9355}
W t_guard_wait_ns: {'n': 5939, 'mean_ms': 4.2867, 'p50_ms': 4.8169, 'p90_ms': 5.3412, 'p99_ms': 9.3716, 'p99.9_ms': 13.3038, 'max_cum_ms': 29.2545}
W guard_owner_rtt_ns: {'n': 5939, 'mean_ms': 4.4534, 'p50_ms': 5.0135, 'p90_ms': 5.5378, 'p99_ms': 9.2406, 'p99.9_ms': 12.9106, 'max_cum_ms': 27.7519}
W guard_queue_ns: {'n': 5939, 'mean_ms': 0.1128, 'p50_ms': 0.105, 'p90_ms': 0.1295, 'p99_ms': 0.1669, 'p99.9_ms': 2.3101, 'max_cum_ms': 16.1001}
W guard_exec_ns: {'n': 5939, 'mean_ms': 3.5856, 'p50_ms': 4.2271, 'p90_ms': 4.4892, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 8.1548}
W t_admit_ns: {'n': 5983, 'mean_ms': 0.0953, 'p50_ms': 0.0886, 'p90_ms': 0.1101, 'p99_ms': 0.1444, 'p99.9_ms': 1.1223, 'max_cum_ms': 21.7487}
W release_processing_ns: {'n': 929527, 'mean_ms': 0.0646, 'p50_ms': 0.0637, 'p90_ms': 0.0845, 'p99_ms': 0.1101, 'p99.9_ms': 0.1403, 'max_cum_ms': 5.7792}
W loop_lag_ns: {'n': 10740, 'mean_ms': 0.8216, 'p50_ms': 0.0076, 'p90_ms': 3.4243, 'p99_ms': 8.4541, 'p99.9_ms': 10.6824, 'max_cum_ms': 21.4172}
W audit_batch_write_ns: {'n': 11830, 'mean_ms': 1.107, 'p50_ms': 1.0199, 'p90_ms': 1.3189, 'p99_ms': 4.0796, 'p99.9_ms': 9.1095, 'max_cum_ms': 40.9418}
W counts: {"admitted": 5983, "audit_enqueued": 11839, "audit_written": 11840, "background_round_trips": 2148, "disposition_ALLOW": 5882, "disposition_BLOCK": 57, "guard_windows": 9786, "lease_refills": 27, "provider_calls": 5882, "provider_connections_opened": 999, "requests_by_round_trips{n=\"0\"}": 5956, "requests_by_round_trips{n=\"1\"}": 27, "shared_state_round_trips": 27, "shed{reason=\"guard_queue\"}": 45}
edge: {"window_s": 61.0, "cores_by_role": {"nginx": 0.123}, "per_proc_util": {"nginx": [0.0, 0.006, 0.007, 0.007, 0.007, 0.007, 0.007, 0.007, 0.007, 0.008, 0.008, 0.008, 0.008, 0.009, 0.009, 0.009, 0.01]}, "per_core_util": {"max": 0.009, "mean": 0.008, "sum": 0.12, "n": 16}, "nic": null, "restarted": [], "loadavg_max": 0.14} nginx cpu-ms/req 1.248
redis: ops/s 857.0 ops/req 8.57 cpu cores 0.007 clients 282 mem 358.3MB ping(us) {'n': 5653, 'p50_us': 499.3, 'p90_us': 534.6, 'p99_us': 649.8, 'p99.9_us': 2094.8, 'max_us': 4033.8, 'mean_us': 513.1}
redis cmdstats: {'info': {'calls_per_s': 0.0, 'usec_per_call': 111.0}, 'ping': {'calls_per_s': 93.4, 'usec_per_call': 0.1}, 'evalsha': {'calls_per_s': 0.5, 'usec_per_call': 10.07}, 'xadd': {'calls_per_s': 196.9, 'usec_per_call': 3.54}, 'mget': {'calls_per_s': 161.5, 'usec_per_call': 0.38}, 'hgetall': {'calls_per_s': 403.9, 'usec_per_call': 0.2}, 'get': {'calls_per_s': 0.5, 'usec_per_call': 0.67}, 'decrby': {'calls_per_s': 0.5, 'usec_per_call': 0.26}}
wire: {'requests_in_window': 6000, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56768.1, 'client_side_to_unit': 8231.4, 'unit_to_provider': 8974.8, 'provider_to_unit': 57473.8, 'unit_to_redis': 5497.9, 'redis_to_unit': 352.0}, 'edge_ip_bytes_per_req': {'edge_to_clients': 56928.0, 'clients_to_edge': 8456.4}, 'olg_resp_body_bytes_mean': {'sse': 67449.9, 'json': 1445.1}}
