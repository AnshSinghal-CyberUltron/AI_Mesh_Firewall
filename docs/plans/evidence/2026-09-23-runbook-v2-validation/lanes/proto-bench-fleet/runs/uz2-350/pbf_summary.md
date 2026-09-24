# uz2-350: strict FAIL | load-knee FAIL (sut, units=2, rate=350)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 105000 (350.0/s) qualified 103089 (343.63/s) FP-blocks 1908 (0.01817) infra 3 (2.857142857142857e-05) drops 0 safety 0
infra reasons: {'block_on_unavailable_sem': 3}
infra detail: {'403 http_403 type=policy_violation code=blocked_by_policy': 3}

T_fw_addon: n=103089 p50=48.0419 p90=55.7283 p99=72.7863 p99.9=81.6399 max=112.9533 mean=39.2421
T_fw_addon_nohold: n=103089 p50=11.2254 p90=15.0829 p99=20.5282 p99.9=26.7664 max=52.8157 mean=11.0194
T_fw_addon_sse: n=72189 p50=51.133 p90=57.4806 p99=73.7285 p99.9=86.4646 max=112.9533 mean=51.2841
T_fw_addon_json: n=30900 p50=11.3017 p90=15.2092 p99=20.8142 p99.9=26.5084 max=52.8157 mean=11.1094
T_addon_first_sse: n=72189 p50=11.1182 p90=15.3848 p99=30.5315 p99.9=37.1312 max=53.5604 mean=11.2298
T_addon_total_sse: n=72189 p50=11.1891 p90=15.0322 p99=20.4302 p99.9=26.8018 max=52.6468 mean=10.9809
T_addon_total_json: n=30900 p50=11.3017 p90=15.2092 p99=20.8142 p99.9=26.5084 max=52.8157 mean=11.1094
T_release_lag_max: n=72189 p50=51.133 p90=57.4806 p99=73.7285 p99.9=86.4646 max=112.9533 mean=51.2841
lateness: n=105000 p50=0.0873 p90=0.0971 p99=0.1089 p99.9=0.1293 max=0.3556 mean=0.0862
loadgen: [{'vm': 'rv-pbf-lg-1', 'busy_max': 9.3, 'busy_mean': 8.7, 'late_max_us': 736, 'conn_opens': 595, 'max_inflight': 573}, {'vm': 'rv-pbf-lg-2', 'busy_max': 9.0, 'busy_mean': 8.5, 'late_max_us': 213, 'conn_opens': 593, 'max_inflight': 573}, {'vm': 'rv-pbf-lg-3', 'busy_max': 9.2, 'busy_mean': 8.7, 'late_max_us': 254, 'conn_opens': 595, 'max_inflight': 573}]  provider_cpu_busy_max: 22.059419442532647
gateway cores total 12.12 cpu-ms/req {'gateway': 34.737, 'workers': 30.732, 'owners': 4.002}
  rv-pbf-unit-2: cores {'launcher': 0.0, 'owner': 0.691, 'owner0': 0.339, 'owner1': 0.352, 'redis': 0.001, 'worker': 5.2} worker util max 0.409 per-core max 0.269 mean 0.249 gpu {'0': {'n': 298, 'sm_mean': 25.6, 'sm_p95': 38.0, 'sm_max': 61.0}, '1': {'n': 298, 'sm_mean': 27.5, 'sm_p95': 40.0, 'sm_max': 53.0}} t_input_p99 15.6631 per-worker admitted {'n': 18, 'min': 2122, 'max': 4384, 'mean': 2913.9, 'max_over_mean': 1.505, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
  rv-pbf-unit-3: cores {'launcher': 0.0, 'owner': 0.705, 'owner0': 0.35, 'owner1': 0.355, 'redis': 0.001, 'worker': 5.521} worker util max 0.351 per-core max 0.284 mean 0.263 gpu {'0': {'n': 298, 'sm_mean': 25.6, 'sm_p95': 39.0, 'sm_max': 51.0}, '1': {'n': 298, 'sm_mean': 26.8, 'sm_p95': 39.0, 'sm_max': 46.0}} t_input_p99 15.7942 per-worker admitted {'n': 18, 'min': 2205, 'max': 3441, 'mean': 2914.5, 'max_over_mean': 1.181, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
W t_input_ns: {'n': 104912, 'mean_ms': 8.4127, 'p50_ms': 8.8474, 'p90_ms': 11.862, 'p99_ms': 15.6631, 'p99.9_ms': 21.1026, 'max_cum_ms': 47.9697}
W t_tokenize_ns: {'n': 104911, 'mean_ms': 3.236, 'p50_ms': 3.2276, 'p90_ms': 5.1446, 'p99_ms': 6.0621, 'p99.9_ms': 6.8485, 'max_cum_ms': 7.9125}
W t_guard_wait_ns: {'n': 104912, 'mean_ms': 4.5532, 'p50_ms': 4.8824, 'p90_ms': 6.9796, 'p99_ms': 10.2892, 'p99.9_ms': 15.401, 'max_cum_ms': 46.4385}
W guard_owner_rtt_ns: {'n': 104912, 'mean_ms': 4.7269, 'p50_ms': 5.079, 'p90_ms': 7.2417, 'p99_ms': 10.027, 'p99.9_ms': 13.9592, 'max_cum_ms': 45.1939}
W guard_queue_ns: {'n': 104909, 'mean_ms': 0.3097, 'p50_ms': 0.107, 'p90_ms': 0.5284, 'p99_ms': 4.0796, 'p99.9_ms': 6.2587, 'max_cum_ms': 10.5603}
W guard_exec_ns: {'n': 104909, 'mean_ms': 3.5661, 'p50_ms': 4.2271, 'p90_ms': 4.4892, 'p99_ms': 6.6519, 'p99.9_ms': 6.8485, 'max_cum_ms': 9.4796}
W t_admit_ns: {'n': 104911, 'mean_ms': 0.0951, 'p50_ms': 0.0886, 'p90_ms': 0.1142, 'p99_ms': 0.1485, 'p99.9_ms': 1.0117, 'max_cum_ms': 6.9353}
W release_processing_ns: {'n': 16129848, 'mean_ms': 0.0649, 'p50_ms': 0.0637, 'p90_ms': 0.0855, 'p99_ms': 0.1142, 'p99.9_ms': 0.1444, 'max_cum_ms': 31.4297}
W loop_lag_ns: {'n': 107190, 'mean_ms': 0.911, 'p50_ms': 0.0124, 'p90_ms': 3.8175, 'p99_ms': 10.027, 'p99.9_ms': 12.7795, 'max_cum_ms': 33.5604}
W audit_batch_write_ns: {'n': 207481, 'mean_ms': 1.1979, 'p50_ms': 1.0035, 'p90_ms': 1.3844, 'p99_ms': 6.4553, 'p99.9_ms': 12.2552, 'max_cum_ms': 49.1949}
W counts: {"admitted": 104911, "audit_enqueued": 208072, "audit_written": 208073, "background_round_trips": 21438, "disposition_ALLOW": 103003, "disposition_BLOCK": 1909, "guard_deadline_expired": 3, "guard_unavailable_findings": 3, "guard_windows": 172073, "lease_refills": 475, "provider_calls": 103003, "provider_connections_opened": 20198, "requests_by_round_trips{n=\"0\"}": 104436, "requests_by_round_trips{n=\"1\"}": 475, "shared_state_round_trips": 475}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 0.586}, "per_proc_util": {"nginx": [0.0, 0.032, 0.032, 0.032, 0.034, 0.034, 0.034, 0.035, 0.036, 0.037, 0.037, 0.039, 0.039, 0.039, 0.04, 0.041, 0.046]}, "per_core_util": {"max": 0.038, "mean": 0.037, "sum": 0.59, "n": 16}, "nic": null, "restarted": [], "loadavg_max": 0.95} nginx cpu-ms/req 1.68
redis: ops/s 1292.3 ops/req 3.692 cpu cores 0.018 clients 289 mem 1378.9MB ping(us) {'n': 28059, 'p50_us': 577.6, 'p90_us': 606.6, 'p99_us': 699.5, 'p99.9_us': 2539.8, 'max_us': 6520.0, 'mean_us': 588.6}
redis cmdstats: {'ping': {'calls_per_s': 93.3, 'usec_per_call': 0.11}, 'hgetall': {'calls_per_s': 358.2, 'usec_per_call': 0.22}, 'decrby': {'calls_per_s': 1.6, 'usec_per_call': 0.32}, 'hello': {'calls_per_s': 0.0, 'usec_per_call': 2.8}, 'mget': {'calls_per_s': 143.3, 'usec_per_call': 0.39}, 'xadd': {'calls_per_s': 692.8, 'usec_per_call': 4.41}, 'info': {'calls_per_s': 0.0, 'usec_per_call': 138.0}, 'get': {'calls_per_s': 1.6, 'usec_per_call': 0.53}, 'evalsha': {'calls_per_s': 1.6, 'usec_per_call': 10.72}}
wire: {'requests_in_window': 105000, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56411.1, 'client_side_to_unit': 8676.3, 'unit_to_provider': 9386.1, 'provider_to_unit': 57103.4, 'unit_to_redis': 5436.8, 'redis_to_unit': 292.9}, 'edge_ip_bytes_per_req': {'edge_to_clients': 56529.5, 'clients_to_edge': 8014.7}, 'olg_resp_body_bytes_mean': {'sse': 67789.1, 'json': 1418.4}}
