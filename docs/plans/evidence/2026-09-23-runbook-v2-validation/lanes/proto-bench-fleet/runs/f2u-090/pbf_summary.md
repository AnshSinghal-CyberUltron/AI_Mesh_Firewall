# f2u-090: strict FAIL | load-knee FAIL (sut, units=2, rate=90)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 27000 (90.0/s) qualified 26480 (88.27/s) FP-blocks 461 (0.01707) infra 59 (0.002185185185185185) drops 0 safety 0
infra reasons: {'http_503': 59, 'incomplete': 59, 'unjoined': 59, 'disposition_missing': 59, 'stage_canon_missing': 59, 'stage_det_missing': 59, 'stage_sem_missing': 59, 'stage_resolve_missing': 59, 'stage_dispatch_missing': 59, 'stage_out_missing': 59, 'stage_audit_missing': 59}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 59}

T_fw_addon: n=26480 p50=10.395 p90=15.0231 p99=53.4181 p99.9=71.3106 max=91.7551 mean=12.6669
T_fw_addon_nohold: n=26480 p50=10.1574 p90=12.9578 p99=15.9297 p99.9=20.1271 max=23.28 mean=9.627
T_fw_addon_sse: n=18529 p50=10.4786 p90=31.3078 p99=54.2253 p99.9=72.0682 max=91.7551 mean=13.9315
T_fw_addon_json: n=7951 p50=10.2404 p90=13.1636 p99=16.5416 p99.9=20.9684 max=23.28 mean=9.7198
T_addon_first_sse: n=18529 p50=10.0803 p90=13.3784 p99=27.6892 p99.9=33.95 max=51.3498 mean=9.8356
T_addon_total_sse: n=18529 p50=10.1331 p90=12.8545 p99=15.7052 p99.9=19.0093 max=23.1221 mean=9.5872
T_addon_total_json: n=7951 p50=10.2404 p90=13.1636 p99=16.5416 p99.9=20.9684 max=23.28 mean=9.7198
T_release_lag_max: n=1854 p50=49.9385 p90=54.2253 p99=72.0682 p99.9=78.6256 max=91.7551 mean=49.5042
lateness: n=27000 p50=0.0873 p90=0.097 p99=0.1083 p99.9=0.1222 max=0.2453 mean=0.0875
loadgen: [{'vm': 'rv-pbf-lg-1', 'busy_max': 18.0, 'busy_mean': 4.4, 'late_max_us': 377, 'conn_opens': 166, 'max_inflight': 163}, {'vm': 'rv-pbf-lg-2', 'busy_max': 18.1, 'busy_mean': 4.3, 'late_max_us': 168, 'conn_opens': 167, 'max_inflight': 164}, {'vm': 'rv-pbf-lg-3', 'busy_max': 12.9, 'busy_mean': 4.4, 'late_max_us': 245, 'conn_opens': 166, 'max_inflight': 163}]  provider_cpu_busy_max: 19.79319131172944
gateway cores total 3.25 cpu-ms/req {'gateway': 36.243, 'workers': 32.159, 'owners': 4.075}
  rv-pbf-unit-2: cores {'launcher': 0.0, 'owner': 0.186, 'owner0': 0.096, 'owner1': 0.089, 'redis': 0.001, 'worker': 1.468} worker util max 0.108 per-core max 0.103 mean 0.071 gpu {'0': {'n': 299, 'sm_mean': 7.0, 'sm_p95': 15.0, 'sm_max': 18.0}, '1': {'n': 299, 'sm_mean': 6.8, 'sm_p95': 15.0, 'sm_max': 22.0}} t_input_p99 12.9106 per-worker admitted {'n': 18, 'min': 441, 'max': 1095, 'mean': 749.4, 'max_over_mean': 1.461, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 6]}
  rv-pbf-unit-3: cores {'launcher': 0.0, 'owner': 0.18, 'owner0': 0.087, 'owner1': 0.093, 'redis': 0.001, 'worker': 1.417} worker util max 0.097 per-core max 0.077 mean 0.069 gpu {'0': {'n': 298, 'sm_mean': 7.1, 'sm_p95': 15.0, 'sm_max': 20.0}, '1': {'n': 298, 'sm_mean': 7.4, 'sm_p95': 15.0, 'sm_max': 20.0}} t_input_p99 12.6484 per-worker admitted {'n': 18, 'min': 401, 'max': 994, 'mean': 748.9, 'max_over_mean': 1.327, 'sheds_per_worker': [0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 2, 2, 2, 3, 3, 3, 4, 5]}
W t_input_ns: {'n': 26911, 'mean_ms': 7.4918, 'p50_ms': 8.1592, 'p90_ms': 10.1581, 'p99_ms': 12.7795, 'p99.9_ms': 16.7117, 'max_cum_ms': 36.429}
W t_tokenize_ns: {'n': 26970, 'mean_ms': 2.7515, 'p50_ms': 2.7689, 'p90_ms': 4.2271, 'p99_ms': 4.8824, 'p99.9_ms': 6.1932, 'max_cum_ms': 8.9355}
W t_guard_wait_ns: {'n': 26911, 'mean_ms': 4.2039, 'p50_ms': 4.7514, 'p90_ms': 5.2101, 'p99_ms': 7.4383, 'p99.9_ms': 11.862, 'max_cum_ms': 29.7032}
W guard_owner_rtt_ns: {'n': 26911, 'mean_ms': 4.3768, 'p50_ms': 4.948, 'p90_ms': 5.4067, 'p99_ms': 7.5694, 'p99.9_ms': 11.3377, 'max_cum_ms': 28.7454}
W guard_queue_ns: {'n': 26911, 'mean_ms': 0.1075, 'p50_ms': 0.106, 'p90_ms': 0.1275, 'p99_ms': 0.1546, 'p99.9_ms': 0.725, 'max_cum_ms': 16.1001}
W guard_exec_ns: {'n': 26911, 'mean_ms': 3.5849, 'p50_ms': 4.2271, 'p90_ms': 4.4892, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 8.1548}
W t_admit_ns: {'n': 26970, 'mean_ms': 0.0929, 'p50_ms': 0.0876, 'p90_ms': 0.106, 'p99_ms': 0.1321, 'p99.9_ms': 1.0281, 'max_cum_ms': 21.7487}
W release_processing_ns: {'n': 4108978, 'mean_ms': 0.0631, 'p50_ms': 0.0627, 'p90_ms': 0.0783, 'p99_ms': 0.1009, 'p99.9_ms': 0.1295, 'max_cum_ms': 26.8838}
W loop_lag_ns: {'n': 107210, 'mean_ms': 0.7436, 'p50_ms': 0.0066, 'p90_ms': 1.9251, 'p99_ms': 7.5039, 'p99.9_ms': 9.6338, 'max_cum_ms': 27.5291}
W audit_batch_write_ns: {'n': 53388, 'mean_ms': 0.978, 'p50_ms': 0.9298, 'p90_ms': 1.1551, 'p99_ms': 1.4336, 'p99.9_ms': 7.6349, 'max_cum_ms': 40.9418}
W counts: {"admitted": 26970, "audit_enqueued": 53407, "audit_written": 53407, "background_round_trips": 21442, "disposition_ALLOW": 26450, "disposition_BLOCK": 461, "guard_windows": 44360, "lease_refills": 122, "provider_calls": 26450, "provider_connections_opened": 3170, "requests_by_round_trips{n=\"0\"}": 26848, "requests_by_round_trips{n=\"1\"}": 122, "shared_state_round_trips": 122, "shed{reason=\"guard_queue\"}": 59}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 0.103}, "per_proc_util": {"nginx": [0.0, 0.004, 0.005, 0.005, 0.005, 0.005, 0.006, 0.006, 0.006, 0.006, 0.007, 0.007, 0.008, 0.008, 0.008, 0.008, 0.009]}, "per_core_util": {"max": 0.009, "mean": 0.007, "sum": 0.11, "n": 16}, "nic": null, "restarted": [], "loadavg_max": 0.25} nginx cpu-ms/req 1.145
redis: ops/s 774.4 ops/req 8.605 cpu cores 0.006 clients 291 mem 1042.4MB ping(us) {'n': 28101, 'p50_us': 565.0, 'p90_us': 598.7, 'p99_us': 717.0, 'p99.9_us': 2006.8, 'max_us': 4259.2, 'mean_us': 578.1}
redis cmdstats: {'info': {'calls_per_s': 0.0, 'usec_per_call': 150.0}, 'ping': {'calls_per_s': 93.5, 'usec_per_call': 0.1}, 'evalsha': {'calls_per_s': 0.4, 'usec_per_call': 11.59}, 'xadd': {'calls_per_s': 177.9, 'usec_per_call': 3.67}, 'mget': {'calls_per_s': 143.3, 'usec_per_call': 0.37}, 'hgetall': {'calls_per_s': 358.4, 'usec_per_call': 0.2}, 'get': {'calls_per_s': 0.4, 'usec_per_call': 0.61}, 'decrby': {'calls_per_s': 0.4, 'usec_per_call': 0.4}}
wire: {'requests_in_window': 27000, 'unit_ip_bytes_per_req': {'unit_to_client_side': 55967.7, 'client_side_to_unit': 9042.6, 'unit_to_provider': 9691.0, 'provider_to_unit': 56648.6, 'unit_to_redis': 5694.4, 'redis_to_unit': 420.3}, 'edge_ip_bytes_per_req': {'edge_to_clients': 56089.4, 'clients_to_edge': 8333.4}, 'olg_resp_body_bytes_mean': {'sse': 67208.0, 'json': 1426.8}}
