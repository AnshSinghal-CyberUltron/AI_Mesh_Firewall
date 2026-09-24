# ub1-040: strict FAIL | load-knee PASS (sut, units=1, rate=40)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 12000 (40.0/s) qualified 11832 (39.44/s) FP-blocks 168 (0.014) infra 0 (0.0) drops 0 safety 0
infra reasons: {}
infra detail: {}

T_fw_addon: n=11832 p50=46.7772 p90=52.9081 p99=70.8029 p99.9=75.4946 max=90.4282 mean=37.1733
T_fw_addon_nohold: n=11832 p50=9.9572 p90=12.5155 p99=15.2492 p99.9=18.4832 max=21.2688 mean=9.3972
T_fw_addon_sse: n=8274 p50=49.7243 p90=53.6034 p99=71.1412 p99.9=78.1256 max=90.4282 mean=49.114
T_fw_addon_json: n=3558 p50=9.9971 p90=12.4172 p99=15.2434 p99.9=17.9202 max=19.3039 mean=9.4055
T_addon_first_sse: n=8274 p50=9.8665 p90=13.1021 p99=29.4973 p99.9=33.3139 max=51.6778 mean=9.6204
T_addon_total_sse: n=8274 p50=9.9312 p90=12.5704 p99=15.2711 p99.9=18.5448 max=21.2688 mean=9.3936
T_addon_total_json: n=3558 p50=9.9971 p90=12.4172 p99=15.2434 p99.9=17.9202 max=19.3039 mean=9.4055
T_release_lag_max: n=8274 p50=49.7243 p90=53.6034 p99=71.1412 p99.9=78.1256 max=90.4282 mean=49.114
lateness: n=12000 p50=0.0857 p90=0.0968 p99=0.1086 p99.9=0.1309 max=0.1738 mean=0.0866
loadgen: [{'vm': 'rv-pbf-lg-1', 'busy_max': 17.2, 'busy_mean': 3.9, 'late_max_us': 186, 'conn_opens': 79, 'max_inflight': 75}, {'vm': 'rv-pbf-lg-2', 'busy_max': 17.2, 'busy_mean': 3.8, 'late_max_us': 172, 'conn_opens': 79, 'max_inflight': 75}, {'vm': 'rv-pbf-lg-3', 'busy_max': 14.1, 'busy_mean': 3.7, 'late_max_us': 260, 'conn_opens': 79, 'max_inflight': 75}]  provider_cpu_busy_max: 19.646151027689328
gateway cores total 1.42 cpu-ms/req {'gateway': 35.65, 'workers': 31.629, 'owners': 4.011}
  rv-pbf-unit-2: cores {'launcher': 0.0, 'owner': 0.16, 'owner0': 0.082, 'owner1': 0.078, 'redis': 0.001, 'worker': 1.261} worker util max 0.09 per-core max 0.074 mean 0.062 gpu {'0': {'n': 298, 'sm_mean': 6.4, 'sm_p95': 13.0, 'sm_max': 18.0}, '1': {'n': 298, 'sm_mean': 6.1, 'sm_p95': 12.0, 'sm_max': 21.0}} t_input_p99 12.2552 per-worker admitted {'n': 18, 'min': 359, 'max': 913, 'mean': 665.9, 'max_over_mean': 1.371, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
W t_input_ns: {'n': 11987, 'mean_ms': 7.2662, 'p50_ms': 7.9626, 'p90_ms': 9.7649, 'p99_ms': 12.2552, 'p99.9_ms': 15.401, 'max_cum_ms': 29.3969}
W t_tokenize_ns: {'n': 11987, 'mean_ms': 2.6276, 'p50_ms': 2.6706, 'p90_ms': 4.0141, 'p99_ms': 4.5548, 'p99.9_ms': 5.7999, 'max_cum_ms': 6.8878}
W t_guard_wait_ns: {'n': 11987, 'mean_ms': 4.1204, 'p50_ms': 4.6858, 'p90_ms': 5.079, 'p99_ms': 7.1762, 'p99.9_ms': 11.2067, 'max_cum_ms': 13.5719}
W guard_owner_rtt_ns: {'n': 11987, 'mean_ms': 4.286, 'p50_ms': 4.8824, 'p90_ms': 5.2756, 'p99_ms': 7.3728, 'p99.9_ms': 11.0756, 'max_cum_ms': 16.225}
W guard_queue_ns: {'n': 11987, 'mean_ms': 0.099, 'p50_ms': 0.0968, 'p90_ms': 0.1193, 'p99_ms': 0.1444, 'p99.9_ms': 0.3502, 'max_cum_ms': 1.0067}
W guard_exec_ns: {'n': 11987, 'mean_ms': 3.5417, 'p50_ms': 4.2271, 'p90_ms': 4.4237, 'p99_ms': 6.4553, 'p99.9_ms': 6.5864, 'max_cum_ms': 9.1313}
W t_admit_ns: {'n': 11987, 'mean_ms': 0.0892, 'p50_ms': 0.0835, 'p90_ms': 0.1019, 'p99_ms': 0.1321, 'p99.9_ms': 1.0568, 'max_cum_ms': 7.6442}
W release_processing_ns: {'n': 1848175, 'mean_ms': 0.0621, 'p50_ms': 0.0617, 'p90_ms': 0.0783, 'p99_ms': 0.1009, 'p99.9_ms': 0.1265, 'max_cum_ms': 0.789}
W loop_lag_ns: {'n': 53640, 'mean_ms': 0.6698, 'p50_ms': 0.0067, 'p90_ms': 1.3681, 'p99_ms': 6.5208, 'p99.9_ms': 9.3716, 'max_cum_ms': 13.4394}
W audit_batch_write_ns: {'n': 23839, 'mean_ms': 1.0061, 'p50_ms': 0.9544, 'p90_ms': 1.2206, 'p99_ms': 1.4664, 'p99.9_ms': 6.9796, 'max_cum_ms': 14.074}
W counts: {"admitted": 11987, "audit_enqueued": 23843, "audit_written": 23842, "background_round_trips": 10728, "disposition_ALLOW": 11819, "disposition_BLOCK": 168, "guard_windows": 19762, "lease_refills": 57, "provider_calls": 11819, "provider_connections_opened": 1711, "requests_by_round_trips{n=\"0\"}": 11930, "requests_by_round_trips{n=\"1\"}": 57, "shared_state_round_trips": 57}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 0.047}, "per_proc_util": {"nginx": [0.0, 0.002, 0.002, 0.002, 0.002, 0.002, 0.003, 0.003, 0.003, 0.003, 0.003, 0.003, 0.003, 0.004, 0.004, 0.004, 0.005]}, "per_core_util": {"max": 0.005, "mean": 0.003, "sum": 0.06, "n": 16}, "nic": null, "restarted": [], "loadavg_max": 0.0} nginx cpu-ms/req 1.191
redis: ops/s 676.6 ops/req 16.915 cpu cores 0.004 clients 253 mem 138.9MB ping(us) {'n': 28292, 'p50_us': 500.5, 'p90_us': 543.9, 'p99_us': 639.8, 'p99.9_us': 1208.8, 'max_us': 3092.9, 'mean_us': 511.6}
redis cmdstats: {'info': {'calls_per_s': 0.0, 'usec_per_call': 135.0}, 'hgetall': {'calls_per_s': 358.9, 'usec_per_call': 0.19}, 'evalsha': {'calls_per_s': 0.2, 'usec_per_call': 12.61}, 'xadd': {'calls_per_s': 79.4, 'usec_per_call': 3.36}, 'decrby': {'calls_per_s': 0.2, 'usec_per_call': 0.42}, 'mget': {'calls_per_s': 143.5, 'usec_per_call': 0.33}, 'get': {'calls_per_s': 0.2, 'usec_per_call': 0.63}, 'ping': {'calls_per_s': 94.1, 'usec_per_call': 0.09}}
wire: {'requests_in_window': 12000, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56634.6, 'client_side_to_unit': 8879.5, 'unit_to_provider': 9665.0, 'provider_to_unit': 57329.7, 'unit_to_redis': 5818.9, 'redis_to_unit': 512.4}, 'edge_ip_bytes_per_req': {'edge_to_clients': 56756.6, 'clients_to_edge': 9244.2}, 'olg_resp_body_bytes_mean': {'sse': 67890.6, 'json': 1435.2}}
