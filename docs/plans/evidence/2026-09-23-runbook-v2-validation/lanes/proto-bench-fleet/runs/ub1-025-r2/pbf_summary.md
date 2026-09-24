# ub1-025-r2: strict FAIL | load-knee PASS (sut, units=1, rate=25)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 7500 (25.0/s) qualified 7407 (24.69/s) FP-blocks 93 (0.0124) infra 0 (0.0) drops 0 safety 0
infra reasons: {}
infra detail: {}

T_fw_addon: n=7407 p50=47.1724 p90=53.4771 p99=71.3051 p99.9=79.1392 max=93.9282 mean=37.637
T_fw_addon_nohold: n=7407 p50=10.2534 p90=12.8892 p99=15.5603 p99.9=19.4007 max=22.1324 mean=9.7478
T_fw_addon_sse: n=5172 p50=50.0339 p90=54.5725 p99=71.7422 p99.9=87.4528 max=93.9282 mean=49.6632
T_fw_addon_json: n=2235 p50=10.3517 p90=13.3468 p99=15.4512 p99.9=17.8649 max=19.9618 mean=9.8072
T_addon_first_sse: n=5172 p50=10.1733 p90=13.4065 p99=29.8574 p99.9=34.3946 max=53.637 mean=10.0074
T_addon_total_sse: n=5172 p50=10.2293 p90=12.6914 p99=15.5729 p99.9=19.4833 max=22.1324 mean=9.7222
T_addon_total_json: n=2235 p50=10.3517 p90=13.3468 p99=15.4512 p99.9=17.8649 max=19.9618 mean=9.8072
T_release_lag_max: n=5172 p50=50.0339 p90=54.5725 p99=71.7422 p99.9=87.4528 max=93.9282 mean=49.6632
lateness: n=7500 p50=0.0932 p90=0.1104 p99=0.1288 p99.9=0.1681 max=0.2282 mean=0.0948
loadgen: [{'vm': 'rv-pbf-lg-1', 'busy_max': 4.0, 'busy_mean': 3.6, 'late_max_us': 192, 'conn_opens': 53, 'max_inflight': 49}, {'vm': 'rv-pbf-lg-2', 'busy_max': 3.9, 'busy_mean': 3.5, 'late_max_us': 240, 'conn_opens': 53, 'max_inflight': 49}, {'vm': 'rv-pbf-lg-3', 'busy_max': 8.5, 'busy_mean': 3.6, 'late_max_us': 265, 'conn_opens': 53, 'max_inflight': 49}]  provider_cpu_busy_max: 18.594809515677678
gateway cores total 0.97 cpu-ms/req {'gateway': 38.986, 'workers': 34.818, 'owners': 4.151}
  rv-pbf-unit-2: cores {'launcher': 0.0, 'owner': 0.103, 'owner0': 0.057, 'owner1': 0.047, 'redis': 0.001, 'worker': 0.868} worker util max 0.073 per-core max 0.072 mean 0.043 gpu {'0': {'n': 299, 'sm_mean': 4.3, 'sm_p95': 11.0, 'sm_max': 17.0}, '1': {'n': 299, 'sm_mean': 3.7, 'sm_p95': 10.0, 'sm_max': 12.0}} t_input_p99 12.2552 per-worker admitted {'n': 18, 'min': 162, 'max': 756, 'mean': 416.2, 'max_over_mean': 1.817, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
W t_input_ns: {'n': 7491, 'mean_ms': 7.4257, 'p50_ms': 8.1592, 'p90_ms': 9.7649, 'p99_ms': 12.2552, 'p99.9_ms': 15.6631, 'max_cum_ms': 29.3969}
W t_tokenize_ns: {'n': 7491, 'mean_ms': 2.6627, 'p50_ms': 2.7034, 'p90_ms': 4.0468, 'p99_ms': 4.5548, 'p99.9_ms': 5.7344, 'max_cum_ms': 7.2352}
W t_guard_wait_ns: {'n': 7491, 'mean_ms': 4.223, 'p50_ms': 4.8169, 'p90_ms': 5.1446, 'p99_ms': 7.2417, 'p99.9_ms': 11.2067, 'max_cum_ms': 14.566}
W guard_owner_rtt_ns: {'n': 7491, 'mean_ms': 4.3868, 'p50_ms': 5.0135, 'p90_ms': 5.3412, 'p99_ms': 7.4383, 'p99.9_ms': 8.8474, 'max_cum_ms': 16.225}
W guard_queue_ns: {'n': 7491, 'mean_ms': 0.1093, 'p50_ms': 0.108, 'p90_ms': 0.1265, 'p99_ms': 0.1505, 'p99.9_ms': 0.5018, 'max_cum_ms': 1.0339}
W guard_exec_ns: {'n': 7491, 'mean_ms': 3.6024, 'p50_ms': 4.2926, 'p90_ms': 4.4892, 'p99_ms': 6.5208, 'p99.9_ms': 6.5864, 'max_cum_ms': 9.1313}
W t_admit_ns: {'n': 7491, 'mean_ms': 0.0962, 'p50_ms': 0.0886, 'p90_ms': 0.1142, 'p99_ms': 0.1505, 'p99.9_ms': 1.0895, 'max_cum_ms': 7.6442}
W release_processing_ns: {'n': 1170175, 'mean_ms': 0.0642, 'p50_ms': 0.0637, 'p90_ms': 0.0824, 'p99_ms': 0.1091, 'p99.9_ms': 0.1382, 'max_cum_ms': 1.4361}
W loop_lag_ns: {'n': 53680, 'mean_ms': 0.6757, 'p50_ms': 0.0186, 'p90_ms': 1.1551, 'p99_ms': 6.5208, 'p99.9_ms': 7.6349, 'max_cum_ms': 13.4394}
W audit_batch_write_ns: {'n': 14879, 'mean_ms': 1.1042, 'p50_ms': 1.1059, 'p90_ms': 1.2861, 'p99_ms': 1.5155, 'p99.9_ms': 7.0451, 'max_cum_ms': 14.074}
W counts: {"admitted": 7491, "audit_enqueued": 14885, "audit_written": 14885, "background_round_trips": 10736, "disposition_ALLOW": 7398, "disposition_BLOCK": 93, "guard_windows": 12306, "lease_refills": 35, "provider_calls": 7398, "provider_connections_opened": 1745, "requests_by_round_trips{n=\"0\"}": 7456, "requests_by_round_trips{n=\"1\"}": 35, "shared_state_round_trips": 35}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 0.042}, "per_proc_util": {"nginx": [0.0, 0.002, 0.002, 0.002, 0.002, 0.002, 0.002, 0.002, 0.003, 0.003, 0.003, 0.003, 0.003, 0.003, 0.003, 0.003, 0.004]}, "per_core_util": {"max": 0.004, "mean": 0.003, "sum": 0.05, "n": 16}, "nic": null, "restarted": [], "loadavg_max": 0.22} nginx cpu-ms/req 1.682
redis: ops/s 269.1 ops/req 10.765 cpu cores 0.002 clients 91 mem 886.8MB ping(us) {'n': 28278, 'p50_us': 489.8, 'p90_us': 585.3, 'p99_us': 683.6, 'p99.9_us': 1528.0, 'max_us': 3358.0, 'mean_us': 518.2}
redis cmdstats: {'info': {'calls_per_s': 0.0, 'usec_per_call': 119.0}, 'hgetall': {'calls_per_s': 89.4, 'usec_per_call': 0.2}, 'evalsha': {'calls_per_s': 0.1, 'usec_per_call': 12.49}, 'xadd': {'calls_per_s': 49.6, 'usec_per_call': 3.18}, 'decrby': {'calls_per_s': 0.1, 'usec_per_call': 0.43}, 'mget': {'calls_per_s': 35.8, 'usec_per_call': 0.35}, 'get': {'calls_per_s': 0.1, 'usec_per_call': 0.63}, 'ping': {'calls_per_s': 94.0, 'usec_per_call': 0.09}}
wire: {'requests_in_window': 7500, 'unit_ip_bytes_per_req': {'unit_to_client_side': 57319.1, 'client_side_to_unit': 9496.0, 'unit_to_provider': 10278.8, 'provider_to_unit': 58026.0, 'unit_to_redis': 6074.6, 'redis_to_unit': 670.0}, 'edge_ip_bytes_per_req': {'edge_to_clients': 57437.0, 'clients_to_edge': 9806.1}, 'olg_resp_body_bytes_mean': {'sse': 68589.7, 'json': 1434.1}}
