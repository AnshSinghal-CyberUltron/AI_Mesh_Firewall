# f2u-100-w1: strict FAIL | load-knee PASS (sut, units=2, rate=100)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 30000 (100.0/s) qualified 29477 (98.26/s) FP-blocks 523 (0.01743) infra 0 (0.0) drops 0 safety 0
infra reasons: {}
infra detail: {}

T_fw_addon: n=29477 p50=10.63 p90=15.6349 p99=53.891 p99.9=71.5783 max=93.7218 mean=13.015
T_fw_addon_nohold: n=29477 p50=10.3745 p90=13.4636 p99=16.0502 p99.9=19.8662 max=23.6138 mean=9.9021
T_fw_addon_sse: n=20624 p50=10.6988 p90=33.6779 p99=55.289 p99.9=71.9059 max=93.7218 mean=14.3089
T_fw_addon_json: n=8853 p50=10.4674 p90=13.5766 p99=16.1171 p99.9=19.8662 max=23.368 mean=10.001
T_addon_first_sse: n=20624 p50=10.2669 p90=13.5981 p99=29.5129 p99.9=34.7008 max=66.2763 mean=10.0889
T_addon_total_sse: n=20624 p50=10.3441 p90=13.4184 p99=15.9831 p99.9=20.0274 max=23.6138 mean=9.8597
T_addon_total_json: n=8853 p50=10.4674 p90=13.5766 p99=16.1171 p99.9=19.8662 max=23.368 mean=10.001
T_release_lag_max: n=2115 p50=49.8449 p90=55.1757 p99=71.9046 p99.9=78.0255 max=93.7218 mean=49.4562
lateness: n=30000 p50=0.0874 p90=0.0966 p99=0.107 p99.9=0.1237 max=0.3485 mean=0.0873
loadgen: [{'vm': 'rv-pbf-lg-1', 'busy_max': 4.9, 'busy_mean': 4.5, 'late_max_us': 316, 'conn_opens': 197, 'max_inflight': 182}, {'vm': 'rv-pbf-lg-2', 'busy_max': 4.8, 'busy_mean': 4.4, 'late_max_us': 348, 'conn_opens': 197, 'max_inflight': 182}, {'vm': 'rv-pbf-lg-3', 'busy_max': 4.7, 'busy_mean': 4.4, 'late_max_us': 221, 'conn_opens': 197, 'max_inflight': 182}]  provider_cpu_busy_max: 19.408581583886843
gateway cores total 3.55 cpu-ms/req {'gateway': 35.605, 'workers': 31.556, 'owners': 4.04}
  rv-pbf-unit-2: cores {'launcher': 0.0, 'owner': 0.204, 'owner0': 0.101, 'owner1': 0.104, 'redis': 0.001, 'worker': 1.578} worker util max 0.115 per-core max 0.088 mean 0.077 gpu {'0': {'n': 298, 'sm_mean': 7.3, 'sm_p95': 15.0, 'sm_max': 22.0}, '1': {'n': 298, 'sm_mean': 7.5, 'sm_p95': 13.0, 'sm_max': 22.0}} t_input_p99 12.7795 per-worker admitted {'n': 18, 'min': 631, 'max': 1216, 'mean': 832.2, 'max_over_mean': 1.461, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
  rv-pbf-unit-3: cores {'launcher': 0.0, 'owner': 0.199, 'owner0': 0.102, 'owner1': 0.097, 'redis': 0.001, 'worker': 1.567} worker util max 0.129 per-core max 0.092 mean 0.076 gpu {'0': {'n': 298, 'sm_mean': 7.5, 'sm_p95': 15.0, 'sm_max': 20.0}, '1': {'n': 298, 'sm_mean': 7.1, 'sm_p95': 14.0, 'sm_max': 17.0}} t_input_p99 12.3863 per-worker admitted {'n': 18, 'min': 363, 'max': 1345, 'mean': 832.6, 'max_over_mean': 1.616, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
W t_input_ns: {'n': 29964, 'mean_ms': 7.5954, 'p50_ms': 8.2903, 'p90_ms': 10.1581, 'p99_ms': 12.6484, 'p99.9_ms': 15.7942, 'max_cum_ms': 36.429}
W t_tokenize_ns: {'n': 29965, 'mean_ms': 2.863, 'p50_ms': 2.8672, 'p90_ms': 4.2926, 'p99_ms': 4.948, 'p99.9_ms': 6.1276, 'max_cum_ms': 8.9355}
W t_guard_wait_ns: {'n': 29964, 'mean_ms': 4.1796, 'p50_ms': 4.7514, 'p90_ms': 5.1446, 'p99_ms': 7.1762, 'p99.9_ms': 11.0756, 'max_cum_ms': 29.7032}
W guard_owner_rtt_ns: {'n': 29964, 'mean_ms': 4.3494, 'p50_ms': 4.948, 'p90_ms': 5.3412, 'p99_ms': 7.3728, 'p99.9_ms': 10.4202, 'max_cum_ms': 28.7454}
W guard_queue_ns: {'n': 29964, 'mean_ms': 0.1058, 'p50_ms': 0.105, 'p90_ms': 0.1244, 'p99_ms': 0.1464, 'p99.9_ms': 0.1833, 'max_cum_ms': 16.1001}
W guard_exec_ns: {'n': 29964, 'mean_ms': 3.5594, 'p50_ms': 4.2271, 'p90_ms': 4.4237, 'p99_ms': 6.4553, 'p99.9_ms': 6.5864, 'max_cum_ms': 8.1548}
W t_admit_ns: {'n': 29965, 'mean_ms': 0.0956, 'p50_ms': 0.0886, 'p90_ms': 0.1132, 'p99_ms': 0.1464, 'p99.9_ms': 1.0895, 'max_cum_ms': 21.7487}
W release_processing_ns: {'n': 4588162, 'mean_ms': 0.063, 'p50_ms': 0.0622, 'p90_ms': 0.0824, 'p99_ms': 0.1101, 'p99.9_ms': 0.1423, 'max_cum_ms': 26.8838}
W loop_lag_ns: {'n': 107230, 'mean_ms': 0.7379, 'p50_ms': 0.0175, 'p90_ms': 1.237, 'p99_ms': 7.6349, 'p99.9_ms': 9.3716, 'max_cum_ms': 27.5291}
W audit_batch_write_ns: {'n': 59376, 'mean_ms': 1.038, 'p50_ms': 0.938, 'p90_ms': 1.2042, 'p99_ms': 4.5548, 'p99.9_ms': 7.0451, 'max_cum_ms': 40.9418}
W counts: {"admitted": 29965, "audit_enqueued": 59393, "audit_written": 59393, "background_round_trips": 21446, "disposition_ALLOW": 29441, "disposition_BLOCK": 523, "guard_windows": 49417, "lease_refills": 138, "provider_calls": 29441, "provider_connections_opened": 5860, "requests_by_round_trips{n=\"0\"}": 29827, "requests_by_round_trips{n=\"1\"}": 138, "shared_state_round_trips": 138}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 0.151}, "per_proc_util": {"nginx": [0.0, 0.151]}, "per_core_util": {"max": 0.013, "mean": 0.009, "sum": 0.15, "n": 16}, "nic": null, "restarted": [], "loadavg_max": 0.36} nginx cpu-ms/req 1.514
redis: ops/s 795.4 ops/req 7.954 cpu cores 0.006 clients 291 mem 859.2MB ping(us) {'n': 28326, 'p50_us': 484.8, 'p90_us': 513.6, 'p99_us': 616.6, 'p99.9_us': 1988.2, 'max_us': 3929.5, 'mean_us': 496.1}
redis cmdstats: {'info': {'calls_per_s': 0.0, 'usec_per_call': 135.0}, 'ping': {'calls_per_s': 94.2, 'usec_per_call': 0.1}, 'evalsha': {'calls_per_s': 0.5, 'usec_per_call': 13.79}, 'xadd': {'calls_per_s': 198.0, 'usec_per_call': 3.61}, 'mget': {'calls_per_s': 143.4, 'usec_per_call': 0.38}, 'hgetall': {'calls_per_s': 358.4, 'usec_per_call': 0.2}, 'get': {'calls_per_s': 0.5, 'usec_per_call': 0.77}, 'decrby': {'calls_per_s': 0.5, 'usec_per_call': 0.44}}
wire: {'requests_in_window': 30000, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56258.4, 'client_side_to_unit': 8099.2, 'unit_to_provider': 8883.1, 'provider_to_unit': 56948.0, 'unit_to_redis': 5663.7, 'redis_to_unit': 403.9}, 'edge_ip_bytes_per_req': {'edge_to_clients': 56369.0, 'clients_to_edge': 8374.9}, 'olg_resp_body_bytes_mean': {'sse': 67577.0, 'json': 1425.6}}
