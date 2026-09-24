# ub2-064: strict FAIL | load-knee PASS (sut, units=2, rate=64)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 19200 (64.0/s) qualified 18910 (63.03/s) FP-blocks 290 (0.0151) infra 0 (0.0) drops 0 safety 0
infra reasons: {}
infra detail: {}

T_fw_addon: n=18910 p50=46.7572 p90=52.9261 p99=70.8751 p99.9=75.1682 max=92.3711 mean=37.2648
T_fw_addon_nohold: n=18910 p50=10.0239 p90=12.6009 p99=15.24 p99.9=19.2035 max=22.4998 mean=9.4438
T_fw_addon_sse: n=13233 p50=49.773 p90=53.7228 p99=71.3562 p99.9=85.6756 max=92.3711 mean=49.1721
T_fw_addon_json: n=5677 p50=10.1006 p90=12.6957 p99=15.4344 p99.9=19.1474 max=20.5813 mean=9.509
T_addon_first_sse: n=13233 p50=9.9521 p90=13.1449 p99=29.6022 p99.9=33.9379 max=49.8713 mean=9.6878
T_addon_total_sse: n=13233 p50=10.004 p90=12.5515 p99=15.1318 p99.9=19.2035 max=22.4998 mean=9.4158
T_addon_total_json: n=5677 p50=10.1006 p90=12.6957 p99=15.4344 p99.9=19.1474 max=20.5813 mean=9.509
T_release_lag_max: n=13233 p50=49.773 p90=53.7228 p99=71.3562 p99.9=85.6756 max=92.3711 mean=49.1721
lateness: n=19200 p50=0.0864 p90=0.0954 p99=0.107 p99.9=0.1285 max=0.2296 mean=0.0862
loadgen: [{'vm': 'rv-pbf-lg-1', 'busy_max': 4.3, 'busy_mean': 4.0, 'late_max_us': 241, 'conn_opens': 128, 'max_inflight': 117}, {'vm': 'rv-pbf-lg-2', 'busy_max': 4.2, 'busy_mean': 3.9, 'late_max_us': 229, 'conn_opens': 128, 'max_inflight': 117}, {'vm': 'rv-pbf-lg-3', 'busy_max': 4.3, 'busy_mean': 4.0, 'late_max_us': 337, 'conn_opens': 128, 'max_inflight': 117}]  provider_cpu_busy_max: 16.240221029132172
gateway cores total 2.35 cpu-ms/req {'gateway': 36.866, 'workers': 32.759, 'owners': 4.094}
  rv-pbf-unit-2: cores {'launcher': 0.0, 'owner': 0.129, 'owner0': 0.069, 'owner1': 0.061, 'redis': 0.001, 'worker': 1.041} worker util max 0.086 per-core max 0.061 mean 0.051 gpu {'0': {'n': 298, 'sm_mean': 4.9, 'sm_p95': 11.0, 'sm_max': 18.0}, '1': {'n': 298, 'sm_mean': 4.6, 'sm_p95': 10.0, 'sm_max': 15.0}} t_input_p99 12.1242 per-worker admitted {'n': 18, 'min': 255, 'max': 865, 'mean': 532.6, 'max_over_mean': 1.624, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
  rv-pbf-unit-3: cores {'launcher': 0.0, 'owner': 0.132, 'owner0': 0.067, 'owner1': 0.065, 'redis': 0.001, 'worker': 1.049} worker util max 0.079 per-core max 0.063 mean 0.051 gpu {'0': {'n': 298, 'sm_mean': 4.8, 'sm_p95': 11.0, 'sm_max': 20.0}, '1': {'n': 298, 'sm_mean': 4.9, 'sm_p95': 11.0, 'sm_max': 18.0}} t_input_p99 12.3863 per-worker admitted {'n': 18, 'min': 298, 'max': 749, 'mean': 532.4, 'max_over_mean': 1.407, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
W t_input_ns: {'n': 19171, 'mean_ms': 7.3569, 'p50_ms': 8.0937, 'p90_ms': 9.8959, 'p99_ms': 12.2552, 'p99.9_ms': 15.401, 'max_cum_ms': 29.3969}
W t_tokenize_ns: {'n': 19171, 'mean_ms': 2.6578, 'p50_ms': 2.7034, 'p90_ms': 4.0796, 'p99_ms': 4.6203, 'p99.9_ms': 5.7344, 'max_cum_ms': 7.1509}
W t_guard_wait_ns: {'n': 19171, 'mean_ms': 4.1764, 'p50_ms': 4.7514, 'p90_ms': 5.1446, 'p99_ms': 7.2417, 'p99.9_ms': 11.2067, 'max_cum_ms': 15.6537}
W guard_owner_rtt_ns: {'n': 19171, 'mean_ms': 4.3453, 'p50_ms': 4.948, 'p90_ms': 5.4067, 'p99_ms': 7.4383, 'p99.9_ms': 10.1581, 'max_cum_ms': 17.0599}
W guard_queue_ns: {'n': 19171, 'mean_ms': 0.1049, 'p50_ms': 0.1039, 'p90_ms': 0.1254, 'p99_ms': 0.1505, 'p99.9_ms': 0.1915, 'max_cum_ms': 1.0339}
W guard_exec_ns: {'n': 19171, 'mean_ms': 3.5868, 'p50_ms': 4.2271, 'p90_ms': 4.4892, 'p99_ms': 6.5208, 'p99.9_ms': 6.6519, 'max_cum_ms': 9.296}
W t_admit_ns: {'n': 19171, 'mean_ms': 0.0921, 'p50_ms': 0.0865, 'p90_ms': 0.1029, 'p99_ms': 0.1275, 'p99.9_ms': 1.0732, 'max_cum_ms': 7.6442}
W release_processing_ns: {'n': 2930169, 'mean_ms': 0.063, 'p50_ms': 0.0632, 'p90_ms': 0.0783, 'p99_ms': 0.0998, 'p99.9_ms': 0.1285, 'max_cum_ms': 0.8203}
W loop_lag_ns: {'n': 107320, 'mean_ms': 0.6575, 'p50_ms': 0.0062, 'p90_ms': 1.2042, 'p99_ms': 6.4553, 'p99.9_ms': 8.4541, 'max_cum_ms': 13.4394}
W audit_batch_write_ns: {'n': 38086, 'mean_ms': 0.9811, 'p50_ms': 0.9298, 'p90_ms': 1.1715, 'p99_ms': 1.4008, 'p99.9_ms': 7.3728, 'max_cum_ms': 14.074}
W counts: {"admitted": 19171, "audit_enqueued": 38091, "audit_written": 38092, "background_round_trips": 21464, "disposition_ALLOW": 18881, "disposition_BLOCK": 290, "guard_windows": 31636, "lease_refills": 92, "provider_calls": 18881, "provider_connections_opened": 2337, "requests_by_round_trips{n=\"0\"}": 19079, "requests_by_round_trips{n=\"1\"}": 92, "shared_state_round_trips": 92}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 0.076}, "per_proc_util": {"nginx": [0.0, 0.003, 0.003, 0.004, 0.004, 0.004, 0.004, 0.004, 0.004, 0.005, 0.005, 0.005, 0.005, 0.006, 0.006, 0.007, 0.007]}, "per_core_util": {"max": 0.005, "mean": 0.005, "sum": 0.08, "n": 16}, "nic": null, "restarted": [], "loadavg_max": 0.24} nginx cpu-ms/req 1.191
redis: ops/s 724.6 ops/req 11.322 cpu cores 0.005 clients 289 mem 335.1MB ping(us) {'n': 28476, 'p50_us': 435.0, 'p90_us': 463.1, 'p99_us': 568.8, 'p99.9_us': 1274.3, 'max_us': 3908.8, 'mean_us': 444.3}
redis cmdstats: {'info': {'calls_per_s': 0.0, 'usec_per_call': 125.0}, 'hgetall': {'calls_per_s': 358.6, 'usec_per_call': 0.2}, 'evalsha': {'calls_per_s': 0.3, 'usec_per_call': 11.73}, 'xadd': {'calls_per_s': 127.0, 'usec_per_call': 3.33}, 'decrby': {'calls_per_s': 0.3, 'usec_per_call': 0.36}, 'mget': {'calls_per_s': 143.4, 'usec_per_call': 0.34}, 'get': {'calls_per_s': 0.3, 'usec_per_call': 0.59}, 'hello': {'calls_per_s': 0.1, 'usec_per_call': 2.5}, 'ping': {'calls_per_s': 94.8, 'usec_per_call': 0.09}}
wire: {'requests_in_window': 19200, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56109.3, 'client_side_to_unit': 9224.6, 'unit_to_provider': 9930.0, 'provider_to_unit': 56793.8, 'unit_to_redis': 5842.4, 'redis_to_unit': 492.6}, 'edge_ip_bytes_per_req': {'edge_to_clients': 56230.9, 'clients_to_edge': 8539.1}, 'olg_resp_body_bytes_mean': {'sse': 67275.9, 'json': 1431.7}}
