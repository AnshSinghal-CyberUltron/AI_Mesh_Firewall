# f4u-100: strict FAIL | load-knee FAIL (sut, units=4, rate=100)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 30000 (100.0/s) qualified 29453 (98.18/s) FP-blocks 506 (0.01687) infra 41 (0.0013666666666666666) drops 0 safety 0
infra reasons: {'http_503': 41, 'incomplete': 41, 'unjoined': 41, 'disposition_missing': 41, 'stage_canon_missing': 41, 'stage_det_missing': 41, 'stage_sem_missing': 41, 'stage_resolve_missing': 41, 'stage_dispatch_missing': 41, 'stage_out_missing': 41, 'stage_audit_missing': 41}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 41}

T_fw_addon: n=29453 p50=10.2761 p90=14.9767 p99=53.3229 p99.9=71.1663 max=87.6414 mean=12.6539
T_fw_addon_nohold: n=29453 p50=10.0458 p90=12.7628 p99=15.4461 p99.9=18.9505 max=23.4053 mean=9.5209
T_fw_addon_sse: n=20611 p50=10.3468 p90=33.6835 p99=54.1297 p99.9=72.1258 max=87.6414 mean=13.9628
T_fw_addon_json: n=8842 p50=10.13 p90=12.9584 p99=15.6505 p99.9=19.2576 max=23.397 mean=9.6029
T_addon_first_sse: n=20611 p50=9.9445 p90=13.2067 p99=29.5288 p99.9=34.3263 max=51.6111 mean=9.7415
T_addon_total_sse: n=20611 p50=10.0107 p90=12.6796 p99=15.4019 p99.9=18.6168 max=23.4053 mean=9.4857
T_addon_total_json: n=8842 p50=10.13 p90=12.9584 p99=15.6505 p99.9=19.2576 max=23.397 mean=9.6029
T_release_lag_max: n=2132 p50=49.7418 p90=54.0402 p99=72.076 p99.9=77.7665 max=87.6414 mean=49.3206
lateness: n=30000 p50=0.0884 p90=0.0977 p99=0.1083 p99.9=0.1274 max=0.2243 mean=0.0883
loadgen: [{'vm': 'rv-pbf-lg-1', 'busy_max': 4.8, 'busy_mean': 4.5, 'late_max_us': 375, 'conn_opens': 195, 'max_inflight': 182}, {'vm': 'rv-pbf-lg-2', 'busy_max': 4.8, 'busy_mean': 4.4, 'late_max_us': 166, 'conn_opens': 195, 'max_inflight': 180}, {'vm': 'rv-pbf-lg-3', 'busy_max': 4.8, 'busy_mean': 4.5, 'late_max_us': 306, 'conn_opens': 197, 'max_inflight': 182}]  provider_cpu_busy_max: 18.712277874019712
gateway cores total 3.87 cpu-ms/req {'gateway': 38.794, 'workers': 34.641, 'owners': 4.136}
  rv-pbf-unit-2: cores {'launcher': 0.0, 'owner': 0.103, 'owner0': 0.043, 'owner1': 0.06, 'redis': 0.001, 'worker': 0.871} worker util max 0.077 per-core max 0.063 mean 0.043 gpu {'0': {'n': 298, 'sm_mean': 2.9, 'sm_p95': 7.0, 'sm_max': 17.0}, '1': {'n': 298, 'sm_mean': 4.8, 'sm_p95': 11.0, 'sm_max': 18.0}} t_input_p99 12.3863 per-worker admitted {'n': 18, 'min': 208, 'max': 736, 'mean': 416.5, 'max_over_mean': 1.767, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 3]}
  rv-pbf-unit-3: cores {'launcher': 0.0, 'owner': 0.102, 'owner0': 0.06, 'owner1': 0.043, 'redis': 0.001, 'worker': 0.863} worker util max 0.066 per-core max 0.052 mean 0.042 gpu {'0': {'n': 298, 'sm_mean': 4.6, 'sm_p95': 11.0, 'sm_max': 22.0}, '1': {'n': 298, 'sm_mean': 3.2, 'sm_p95': 9.0, 'sm_max': 16.0}} t_input_p99 12.2552 per-worker admitted {'n': 18, 'min': 190, 'max': 600, 'mean': 415.9, 'max_over_mean': 1.443, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 2, 2]}
  rv-pbf-unit-4: cores {'launcher': 0.0, 'owner': 0.105, 'owner0': 0.056, 'owner1': 0.049, 'redis': 0.001, 'worker': 0.874} worker util max 0.062 per-core max 0.053 mean 0.043 gpu {'0': {'n': 298, 'sm_mean': 4.2, 'sm_p95': 11.0, 'sm_max': 15.0}, '1': {'n': 298, 'sm_mean': 3.6, 'sm_p95': 10.0, 'sm_max': 14.0}} t_input_p99 12.3863 per-worker admitted {'n': 18, 'min': 131, 'max': 542, 'mean': 416.2, 'max_over_mean': 1.302, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 2, 2]}
  rv-pbf-unit-5: cores {'launcher': 0.0, 'owner': 0.102, 'owner0': 0.055, 'owner1': 0.047, 'redis': 0.001, 'worker': 0.844} worker util max 0.089 per-core max 0.058 mean 0.041 gpu {'0': {'n': 298, 'sm_mean': 4.0, 'sm_p95': 10.0, 'sm_max': 14.0}, '1': {'n': 298, 'sm_mean': 3.5, 'sm_p95': 11.0, 'sm_max': 17.0}} t_input_p99 12.1242 per-worker admitted {'n': 18, 'min': 160, 'max': 940, 'mean': 416.6, 'max_over_mean': 2.256, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 2, 2, 2, 3]}
W t_input_ns: {'n': 29935, 'mean_ms': 7.3533, 'p50_ms': 8.0937, 'p90_ms': 9.7649, 'p99_ms': 12.3863, 'p99.9_ms': 16.0563, 'max_cum_ms': 36.429}
W t_tokenize_ns: {'n': 29974, 'mean_ms': 2.625, 'p50_ms': 2.6378, 'p90_ms': 3.9813, 'p99_ms': 4.4892, 'p99.9_ms': 5.9965, 'max_cum_ms': 8.9355}
W t_guard_wait_ns: {'n': 29935, 'mean_ms': 4.2017, 'p50_ms': 4.7514, 'p90_ms': 5.2101, 'p99_ms': 7.3073, 'p99.9_ms': 11.5999, 'max_cum_ms': 30.0756}
W guard_owner_rtt_ns: {'n': 29935, 'mean_ms': 4.3647, 'p50_ms': 4.948, 'p90_ms': 5.4067, 'p99_ms': 7.5694, 'p99.9_ms': 10.6824, 'max_cum_ms': 28.7454}
W guard_queue_ns: {'n': 29935, 'mean_ms': 0.1065, 'p50_ms': 0.106, 'p90_ms': 0.1244, 'p99_ms': 0.1464, 'p99.9_ms': 0.8397, 'max_cum_ms': 16.1001}
W guard_exec_ns: {'n': 29935, 'mean_ms': 3.5967, 'p50_ms': 4.2271, 'p90_ms': 4.4892, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 9.1328}
W t_admit_ns: {'n': 29974, 'mean_ms': 0.0943, 'p50_ms': 0.0886, 'p90_ms': 0.106, 'p99_ms': 0.1362, 'p99.9_ms': 1.0035, 'max_cum_ms': 21.7487}
W release_processing_ns: {'n': 4585952, 'mean_ms': 0.0636, 'p50_ms': 0.0632, 'p90_ms': 0.0794, 'p99_ms': 0.1029, 'p99.9_ms': 0.1362, 'max_cum_ms': 26.8838}
W loop_lag_ns: {'n': 214590, 'mean_ms': 0.6977, 'p50_ms': 0.0116, 'p90_ms': 1.1878, 'p99_ms': 6.914, 'p99.9_ms': 8.3558, 'max_cum_ms': 27.862}
W audit_batch_write_ns: {'n': 59344, 'mean_ms': 0.9732, 'p50_ms': 0.9216, 'p90_ms': 1.1387, 'p99_ms': 1.4172, 'p99.9_ms': 7.4383, 'max_cum_ms': 40.9418}
W counts: {"admitted": 29974, "audit_enqueued": 59353, "audit_written": 59352, "background_round_trips": 42918, "disposition_ALLOW": 29429, "disposition_BLOCK": 506, "guard_windows": 49340, "lease_refills": 143, "provider_calls": 29429, "provider_connections_opened": 4930, "requests_by_round_trips{n=\"0\"}": 29831, "requests_by_round_trips{n=\"1\"}": 143, "shared_state_round_trips": 143, "shed{reason=\"guard_queue\"}": 41}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 0.125}, "per_proc_util": {"nginx": [0.0, 0.005, 0.006, 0.006, 0.007, 0.007, 0.007, 0.007, 0.008, 0.008, 0.008, 0.008, 0.008, 0.01, 0.01, 0.01, 0.01]}, "per_core_util": {"max": 0.008, "mean": 0.008, "sum": 0.13, "n": 16}, "nic": null, "restarted": [], "loadavg_max": 0.46} nginx cpu-ms/req 1.259
redis: ops/s 794.1 ops/req 7.941 cpu cores 0.007 clients 363 mem 1061.1MB ping(us) {'n': 28315, 'p50_us': 491.1, 'p90_us': 519.5, 'p99_us': 636.1, 'p99.9_us': 1793.5, 'max_us': 4549.4, 'mean_us': 503.4}
redis cmdstats: {'info': {'calls_per_s': 0.0, 'usec_per_call': 150.0}, 'ping': {'calls_per_s': 94.2, 'usec_per_call': 0.1}, 'evalsha': {'calls_per_s': 0.5, 'usec_per_call': 10.83}, 'xadd': {'calls_per_s': 197.8, 'usec_per_call': 3.71}, 'mget': {'calls_per_s': 143.0, 'usec_per_call': 0.39}, 'hgetall': {'calls_per_s': 357.6, 'usec_per_call': 0.22}, 'get': {'calls_per_s': 0.5, 'usec_per_call': 0.62}, 'decrby': {'calls_per_s': 0.5, 'usec_per_call': 0.38}}
wire: {'requests_in_window': 30000, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56209.2, 'client_side_to_unit': 9789.8, 'unit_to_provider': 10306.2, 'provider_to_unit': 56897.6, 'unit_to_redis': 5897.1, 'redis_to_unit': 502.9}, 'edge_ip_bytes_per_req': {'edge_to_clients': 56320.3, 'clients_to_edge': 8307.1}, 'olg_resp_body_bytes_mean': {'sse': 67560.5, 'json': 1425.3}}
