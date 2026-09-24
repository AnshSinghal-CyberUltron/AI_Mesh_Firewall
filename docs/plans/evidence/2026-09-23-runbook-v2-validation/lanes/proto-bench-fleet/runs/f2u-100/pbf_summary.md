# f2u-100: strict FAIL | load-knee FAIL (sut, units=2, rate=100)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 30000 (100.0/s) qualified 29420 (98.07/s) FP-blocks 520 (0.01733) infra 60 (0.002) drops 0 safety 0
infra reasons: {'http_503': 60, 'incomplete': 60, 'unjoined': 60, 'disposition_missing': 60, 'stage_canon_missing': 60, 'stage_det_missing': 60, 'stage_sem_missing': 60, 'stage_resolve_missing': 60, 'stage_dispatch_missing': 60, 'stage_out_missing': 60, 'stage_audit_missing': 60}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 60}

T_fw_addon: n=29420 p50=10.4416 p90=15.227 p99=53.3642 p99.9=71.4184 max=90.3832 mean=12.7191
T_fw_addon_nohold: n=29420 p50=10.202 p90=13.2224 p99=16.1297 p99.9=20.6052 max=34.4748 mean=9.7223
T_fw_addon_sse: n=20583 p50=10.5177 p90=31.0332 p99=54.1492 p99.9=72.1066 max=90.3832 mean=13.9739
T_fw_addon_json: n=8837 p50=10.2733 p90=13.3531 p99=16.1245 p99.9=20.8985 max=34.4748 mean=9.7963
T_addon_first_sse: n=20583 p50=10.1166 p90=13.4723 p99=28.3214 p99.9=33.6903 max=51.0213 mean=9.9251
T_addon_total_sse: n=20583 p50=10.1809 p90=13.17 p99=16.1494 p99.9=20.3806 max=26.9733 mean=9.6905
T_addon_total_json: n=8837 p50=10.2733 p90=13.3531 p99=16.1245 p99.9=20.8985 max=34.4748 mean=9.7963
T_release_lag_max: n=2034 p50=49.8914 p90=54.1958 p99=72.1066 p99.9=86.7997 max=90.3832 mean=49.4527
lateness: n=30000 p50=0.0881 p90=0.0975 p99=0.1081 p99.9=0.125 max=0.3434 mean=0.0881
loadgen: [{'vm': 'rv-pbf-lg-1', 'busy_max': 4.9, 'busy_mean': 4.4, 'late_max_us': 424, 'conn_opens': 196, 'max_inflight': 182}, {'vm': 'rv-pbf-lg-2', 'busy_max': 4.8, 'busy_mean': 4.4, 'late_max_us': 275, 'conn_opens': 198, 'max_inflight': 181}, {'vm': 'rv-pbf-lg-3', 'busy_max': 4.7, 'busy_mean': 4.4, 'late_max_us': 343, 'conn_opens': 196, 'max_inflight': 181}]  provider_cpu_busy_max: 13.971831498668251
gateway cores total 3.59 cpu-ms/req {'gateway': 36.027, 'workers': 31.943, 'owners': 4.075}
  rv-pbf-unit-2: cores {'launcher': 0.0, 'owner': 0.204, 'owner0': 0.099, 'owner1': 0.105, 'redis': 0.001, 'worker': 1.609} worker util max 0.149 per-core max 0.09 mean 0.078 gpu {'0': {'n': 298, 'sm_mean': 7.2, 'sm_p95': 15.0, 'sm_max': 24.0}, '1': {'n': 298, 'sm_mean': 7.6, 'sm_p95': 15.0, 'sm_max': 22.0}} t_input_p99 12.7795 per-worker admitted {'n': 18, 'min': 596, 'max': 1529, 'mean': 832.6, 'max_over_mean': 1.837, 'sheds_per_worker': [0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 5, 7]}
  rv-pbf-unit-3: cores {'launcher': 0.0, 'owner': 0.202, 'owner0': 0.104, 'owner1': 0.099, 'redis': 0.001, 'worker': 1.574} worker util max 0.119 per-core max 0.102 mean 0.076 gpu {'0': {'n': 298, 'sm_mean': 7.8, 'sm_p95': 16.0, 'sm_max': 24.0}, '1': {'n': 298, 'sm_mean': 7.8, 'sm_p95': 16.0, 'sm_max': 24.0}} t_input_p99 12.5174 per-worker admitted {'n': 18, 'min': 577, 'max': 1214, 'mean': 831.9, 'max_over_mean': 1.459, 'sheds_per_worker': [0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 2, 2, 2, 3, 4, 6]}
W t_input_ns: {'n': 29903, 'mean_ms': 7.4883, 'p50_ms': 8.1592, 'p90_ms': 10.027, 'p99_ms': 12.6484, 'p99.9_ms': 16.0563, 'max_cum_ms': 36.429}
W t_tokenize_ns: {'n': 29960, 'mean_ms': 2.72, 'p50_ms': 2.7361, 'p90_ms': 4.1124, 'p99_ms': 4.6858, 'p99.9_ms': 6.0621, 'max_cum_ms': 8.9355}
W t_guard_wait_ns: {'n': 29903, 'mean_ms': 4.2237, 'p50_ms': 4.7514, 'p90_ms': 5.2756, 'p99_ms': 7.4383, 'p99.9_ms': 11.5999, 'max_cum_ms': 29.7032}
W guard_owner_rtt_ns: {'n': 29903, 'mean_ms': 4.3881, 'p50_ms': 4.948, 'p90_ms': 5.4723, 'p99_ms': 7.6349, 'p99.9_ms': 10.9445, 'max_cum_ms': 28.7454}
W guard_queue_ns: {'n': 29903, 'mean_ms': 0.107, 'p50_ms': 0.105, 'p90_ms': 0.1254, 'p99_ms': 0.1526, 'p99.9_ms': 0.8315, 'max_cum_ms': 16.1001}
W guard_exec_ns: {'n': 29903, 'mean_ms': 3.5921, 'p50_ms': 4.2271, 'p90_ms': 4.5548, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 8.1548}
W t_admit_ns: {'n': 29960, 'mean_ms': 0.0954, 'p50_ms': 0.0886, 'p90_ms': 0.1101, 'p99_ms': 0.1444, 'p99.9_ms': 1.0568, 'max_cum_ms': 21.7487}
W release_processing_ns: {'n': 4579403, 'mean_ms': 0.0635, 'p50_ms': 0.0632, 'p90_ms': 0.0804, 'p99_ms': 0.105, 'p99.9_ms': 0.1362, 'max_cum_ms': 26.4251}
W loop_lag_ns: {'n': 107240, 'mean_ms': 0.7397, 'p50_ms': 0.0088, 'p90_ms': 2.0398, 'p99_ms': 7.5039, 'p99.9_ms': 9.3716, 'max_cum_ms': 27.5291}
W audit_batch_write_ns: {'n': 59256, 'mean_ms': 1.0303, 'p50_ms': 0.9626, 'p90_ms': 1.2206, 'p99_ms': 2.2118, 'p99.9_ms': 7.766, 'max_cum_ms': 40.9418}
W counts: {"admitted": 29960, "audit_enqueued": 59285, "audit_written": 59283, "background_round_trips": 21448, "disposition_ALLOW": 29383, "disposition_BLOCK": 520, "guard_windows": 49261, "lease_refills": 142, "provider_calls": 29383, "provider_connections_opened": 4517, "requests_by_round_trips{n=\"0\"}": 29818, "requests_by_round_trips{n=\"1\"}": 142, "shared_state_round_trips": 142, "shed{reason=\"guard_queue\"}": 59}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 0.124}, "per_proc_util": {"nginx": [0.0, 0.006, 0.007, 0.007, 0.007, 0.007, 0.007, 0.007, 0.008, 0.008, 0.008, 0.008, 0.008, 0.009, 0.009, 0.01, 0.011]}, "per_core_util": {"max": 0.008, "mean": 0.008, "sum": 0.13, "n": 16}, "nic": null, "restarted": [], "loadavg_max": 0.19} nginx cpu-ms/req 1.246
redis: ops/s 795.0 ops/req 7.95 cpu cores 0.006 clients 291 mem 491.9MB ping(us) {'n': 28302, 'p50_us': 493.1, 'p90_us': 520.0, 'p99_us': 622.2, 'p99.9_us': 2014.6, 'max_us': 4994.5, 'mean_us': 503.7}
redis cmdstats: {'info': {'calls_per_s': 0.0, 'usec_per_call': 149.0}, 'ping': {'calls_per_s': 94.2, 'usec_per_call': 0.1}, 'evalsha': {'calls_per_s': 0.5, 'usec_per_call': 11.36}, 'xadd': {'calls_per_s': 197.6, 'usec_per_call': 3.63}, 'mget': {'calls_per_s': 143.4, 'usec_per_call': 0.38}, 'hgetall': {'calls_per_s': 358.4, 'usec_per_call': 0.21}, 'get': {'calls_per_s': 0.5, 'usec_per_call': 0.63}, 'decrby': {'calls_per_s': 0.5, 'usec_per_call': 0.38}}
wire: {'requests_in_window': 30000, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56143.5, 'client_side_to_unit': 9022.2, 'unit_to_provider': 9746.3, 'provider_to_unit': 56827.6, 'unit_to_redis': 5657.4, 'redis_to_unit': 403.3}, 'edge_ip_bytes_per_req': {'edge_to_clients': 56255.4, 'clients_to_edge': 8322.0}, 'olg_resp_body_bytes_mean': {'sse': 67573.3, 'json': 1425.3}}
