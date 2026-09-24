# ub1-025: strict FAIL | load-knee PASS (sut, units=1, rate=25)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 7500 (25.0/s) qualified 7411 (24.7/s) FP-blocks 89 (0.01187) infra 0 (0.0) drops 0 safety 0
infra reasons: {}
infra detail: {}

T_fw_addon: n=7411 p50=47.0293 p90=53.1186 p99=71.0285 p99.9=75.3846 max=96.8783 mean=37.4093
T_fw_addon_nohold: n=7411 p50=10.0702 p90=12.5252 p99=15.0489 p99.9=17.6247 max=20.1517 mean=9.5553
T_fw_addon_sse: n=5176 p50=49.8373 p90=53.9205 p99=71.6334 p99.9=87.0054 max=96.8783 mean=49.4101
T_fw_addon_json: n=2235 p50=10.1074 p90=12.875 p99=15.0674 p99.9=16.9053 max=18.1152 mean=9.6168
T_addon_first_sse: n=5176 p50=9.9974 p90=13.0932 p99=27.3397 p99.9=33.3622 max=53.3787 mean=9.7659
T_addon_total_sse: n=5176 p50=10.0483 p90=12.4357 p99=15.0228 p99.9=17.9419 max=20.1517 mean=9.5287
T_addon_total_json: n=2235 p50=10.1074 p90=12.875 p99=15.0674 p99.9=16.9053 max=18.1152 mean=9.6168
T_release_lag_max: n=5176 p50=49.8373 p90=53.9205 p99=71.6334 p99.9=87.0054 max=96.8783 mean=49.4101
lateness: n=7500 p50=0.0953 p90=0.1167 p99=0.1376 p99.9=0.1566 max=0.1994 mean=0.0963
loadgen: [{'vm': 'rv-pbf-lg-1', 'busy_max': 17.3, 'busy_mean': 3.6, 'late_max_us': 340, 'conn_opens': 53, 'max_inflight': 49}, {'vm': 'rv-pbf-lg-2', 'busy_max': 14.0, 'busy_mean': 3.7, 'late_max_us': 349, 'conn_opens': 53, 'max_inflight': 49}, {'vm': 'rv-pbf-lg-3', 'busy_max': 13.0, 'busy_mean': 3.7, 'late_max_us': 199, 'conn_opens': 53, 'max_inflight': 49}]  provider_cpu_busy_max: 17.85917204877734
gateway cores total 0.95 cpu-ms/req {'gateway': 38.004, 'workers': 33.921, 'owners': 4.066}
  rv-pbf-unit-2: cores {'launcher': 0.0, 'owner': 0.101, 'owner0': 0.045, 'owner1': 0.057, 'redis': 0.001, 'worker': 0.845} worker util max 0.065 per-core max 0.057 mean 0.042 gpu {'0': {'n': 298, 'sm_mean': 3.1, 'sm_p95': 7.0, 'sm_max': 15.0}, '1': {'n': 298, 'sm_mean': 4.6, 'sm_p95': 10.0, 'sm_max': 16.0}} t_input_p99 12.1242 per-worker admitted {'n': 18, 'min': 261, 'max': 618, 'mean': 416.4, 'max_over_mean': 1.484, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
W t_input_ns: {'n': 7495, 'mean_ms': 7.2836, 'p50_ms': 8.0282, 'p90_ms': 9.7649, 'p99_ms': 12.1242, 'p99.9_ms': 14.7456, 'max_cum_ms': 29.3969}
W t_tokenize_ns: {'n': 7495, 'mean_ms': 2.6272, 'p50_ms': 2.6706, 'p90_ms': 4.0141, 'p99_ms': 4.5548, 'p99.9_ms': 5.9965, 'max_cum_ms': 6.8878}
W t_guard_wait_ns: {'n': 7495, 'mean_ms': 4.1285, 'p50_ms': 4.6858, 'p90_ms': 5.079, 'p99_ms': 7.1107, 'p99.9_ms': 10.4202, 'max_cum_ms': 13.1764}
W guard_owner_rtt_ns: {'n': 7495, 'mean_ms': 4.2951, 'p50_ms': 4.8824, 'p90_ms': 5.2756, 'p99_ms': 7.3073, 'p99.9_ms': 10.027, 'max_cum_ms': 16.225}
W guard_queue_ns: {'n': 7495, 'mean_ms': 0.1036, 'p50_ms': 0.1029, 'p90_ms': 0.1193, 'p99_ms': 0.1403, 'p99.9_ms': 0.1731, 'max_cum_ms': 0.8139}
W guard_exec_ns: {'n': 7495, 'mean_ms': 3.5467, 'p50_ms': 4.2271, 'p90_ms': 4.4237, 'p99_ms': 6.4553, 'p99.9_ms': 6.5208, 'max_cum_ms': 9.1313}
W t_admit_ns: {'n': 7495, 'mean_ms': 0.0988, 'p50_ms': 0.0906, 'p90_ms': 0.1193, 'p99_ms': 0.1505, 'p99.9_ms': 1.1059, 'max_cum_ms': 7.6442}
W release_processing_ns: {'n': 1169454, 'mean_ms': 0.0628, 'p50_ms': 0.0622, 'p90_ms': 0.0814, 'p99_ms': 0.106, 'p99.9_ms': 0.1341, 'max_cum_ms': 0.6618}
W loop_lag_ns: {'n': 53670, 'mean_ms': 0.6194, 'p50_ms': 0.0157, 'p90_ms': 1.1059, 'p99_ms': 5.9965, 'p99.9_ms': 7.7005, 'max_cum_ms': 10.9693}
W audit_batch_write_ns: {'n': 14874, 'mean_ms': 1.0297, 'p50_ms': 0.9789, 'p90_ms': 1.2206, 'p99_ms': 1.5647, 'p99.9_ms': 6.6519, 'max_cum_ms': 8.9225}
W counts: {"admitted": 7495, "audit_enqueued": 14874, "audit_written": 14874, "background_round_trips": 10734, "disposition_ALLOW": 7406, "disposition_BLOCK": 89, "guard_windows": 12310, "lease_refills": 33, "provider_calls": 7406, "provider_connections_opened": 1681, "requests_by_round_trips{n=\"0\"}": 7462, "requests_by_round_trips{n=\"1\"}": 33, "shared_state_round_trips": 33}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 0.042}, "per_proc_util": {"nginx": [0.0, 0.001, 0.002, 0.002, 0.002, 0.002, 0.002, 0.003, 0.003, 0.003, 0.003, 0.003, 0.003, 0.003, 0.003, 0.004, 0.004]}, "per_core_util": {"max": 0.006, "mean": 0.003, "sum": 0.05, "n": 16}, "nic": null, "restarted": [], "loadavg_max": 0.09} nginx cpu-ms/req 1.704
redis: ops/s 647.4 ops/req 25.894 cpu cores 0.004 clients 253 mem 57.3MB ping(us) {'n': 28504, 'p50_us': 424.2, 'p90_us': 493.7, 'p99_us': 564.3, 'p99.9_us': 909.6, 'max_us': 4095.2, 'mean_us': 436.2}
redis cmdstats: {'info': {'calls_per_s': 0.0, 'usec_per_call': 136.0}, 'hgetall': {'calls_per_s': 358.9, 'usec_per_call': 0.19}, 'evalsha': {'calls_per_s': 0.1, 'usec_per_call': 16.48}, 'xadd': {'calls_per_s': 49.6, 'usec_per_call': 3.59}, 'decrby': {'calls_per_s': 0.1, 'usec_per_call': 0.52}, 'mget': {'calls_per_s': 143.6, 'usec_per_call': 0.34}, 'get': {'calls_per_s': 0.1, 'usec_per_call': 0.7}, 'hello': {'calls_per_s': 0.1, 'usec_per_call': 2.56}, 'ping': {'calls_per_s': 94.8, 'usec_per_call': 0.08}}
wire: {'requests_in_window': 7500, 'unit_ip_bytes_per_req': {'unit_to_client_side': 57343.5, 'client_side_to_unit': 9429.6, 'unit_to_provider': 10217.3, 'provider_to_unit': 58070.3, 'unit_to_redis': 6080.9, 'redis_to_unit': 673.1}, 'edge_ip_bytes_per_req': {'edge_to_clients': 57460.5, 'clients_to_edge': 9832.0}, 'olg_resp_body_bytes_mean': {'sse': 68566.3, 'json': 1433.2}}
