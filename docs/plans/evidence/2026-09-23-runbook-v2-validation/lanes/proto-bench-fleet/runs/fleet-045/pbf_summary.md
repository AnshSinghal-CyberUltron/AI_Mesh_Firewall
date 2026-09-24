# fleet-045: strict FAIL | load-knee PASS (sut, units=3, rate=45)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 13500 (45.0/s) qualified 13243 (44.14/s) FP-blocks 257 (0.01904) infra 0 (0.0) drops 0 safety 0
infra reasons: {}
infra detail: {}

T_fw_addon: n=13243 p50=10.6478 p90=15.1494 p99=53.4714 p99.9=70.9295 max=89.9661 mean=12.9202
T_fw_addon_nohold: n=13243 p50=10.3995 p90=13.2417 p99=15.9972 p99.9=19.3889 max=24.3997 mean=9.8327
T_fw_addon_sse: n=9273 p50=10.7377 p90=32.9314 p99=54.1553 p99.9=71.1532 max=89.9661 mean=14.2206
T_fw_addon_json: n=3970 p50=10.4308 p90=13.4565 p99=16.0345 p99.9=18.0982 max=19.5264 mean=9.8828
T_addon_first_sse: n=9273 p50=10.3464 p90=13.5625 p99=28.0469 p99.9=34.2832 max=55.0359 mean=10.0642
T_addon_total_sse: n=9273 p50=10.3858 p90=13.1385 p99=15.9497 p99.9=20.1944 max=24.3997 mean=9.8112
T_addon_total_json: n=3970 p50=10.4308 p90=13.4565 p99=16.0345 p99.9=18.0982 max=19.5264 mean=9.8828
T_release_lag_max: n=944 p50=50.1207 p90=54.1244 p99=71.1532 p99.9=89.9661 max=89.9661 mean=49.5787
lateness: n=13500 p50=0.088 p90=0.0975 p99=0.108 p99.9=0.1204 max=0.2366 mean=0.0881
loadgen: [{'vm': 'rv-pbf-lg-4', 'busy_max': 17.9, 'busy_mean': 4.9, 'late_max_us': 540, 'conn_opens': 245, 'max_inflight': 235}]  provider_cpu_busy_max: 20.80677220063427
gateway cores total 1.67 cpu-ms/req {'gateway': 37.221, 'workers': 32.933, 'owners': 4.267}
  rv-pbf-unit-6: cores {'launcher': 0.0, 'owner': 0.064, 'owner0': 0.064, 'redis': 0.001, 'worker': 0.499} worker util max 0.097 per-core max 0.087 mean 0.078 gpu {'0': {'n': 298, 'sm_mean': 4.7, 'sm_p95': 10.0, 'sm_max': 16.0}} t_input_p99 12.9106 per-worker admitted {'n': 6, 'min': 581, 'max': 906, 'mean': 748.8, 'max_over_mean': 1.21, 'sheds_per_worker': [0, 0, 0, 0, 0, 0]}
  rv-pbf-unit-8: cores {'launcher': 0.0, 'owner': 0.064, 'owner0': 0.064, 'redis': 0.001, 'worker': 0.501} worker util max 0.105 per-core max 0.091 mean 0.078 gpu {'0': {'n': 298, 'sm_mean': 4.6, 'sm_p95': 9.0, 'sm_max': 15.0}} t_input_p99 12.7795 per-worker admitted {'n': 6, 'min': 450, 'max': 955, 'mean': 748.7, 'max_over_mean': 1.276, 'sheds_per_worker': [0, 0, 0, 0, 0, 0]}
  rv-pbf-unit-9: cores {'launcher': 0.0, 'owner': 0.064, 'owner0': 0.064, 'redis': 0.001, 'worker': 0.477} worker util max 0.105 per-core max 0.082 mean 0.075 gpu {'0': {'n': 298, 'sm_mean': 4.7, 'sm_p95': 11.0, 'sm_max': 13.0}} t_input_p99 12.9106 per-worker admitted {'n': 6, 'min': 447, 'max': 1057, 'mean': 749.8, 'max_over_mean': 1.41, 'sheds_per_worker': [0, 0, 0, 0, 0, 0]}
W t_input_ns: {'n': 13484, 'mean_ms': 7.6728, 'p50_ms': 8.4541, 'p90_ms': 10.2892, 'p99_ms': 12.9106, 'p99.9_ms': 15.6631, 'max_cum_ms': 27.6245}
W t_tokenize_ns: {'n': 13484, 'mean_ms': 2.7522, 'p50_ms': 2.7689, 'p90_ms': 4.2271, 'p99_ms': 4.8824, 'p99.9_ms': 6.0621, 'max_cum_ms': 7.2829}
W t_guard_wait_ns: {'n': 13484, 'mean_ms': 4.3514, 'p50_ms': 4.8824, 'p90_ms': 5.4067, 'p99_ms': 7.5039, 'p99.9_ms': 10.8134, 'max_cum_ms': 16.0843}
W guard_owner_rtt_ns: {'n': 13484, 'mean_ms': 4.5345, 'p50_ms': 5.079, 'p90_ms': 5.6033, 'p99_ms': 7.7005, 'p99.9_ms': 10.2892, 'max_cum_ms': 16.7258}
W guard_queue_ns: {'n': 13484, 'mean_ms': 0.1215, 'p50_ms': 0.1183, 'p90_ms': 0.1444, 'p99_ms': 0.1772, 'p99.9_ms': 0.255, 'max_cum_ms': 4.6197}
W guard_exec_ns: {'n': 13484, 'mean_ms': 3.6801, 'p50_ms': 4.2926, 'p90_ms': 4.6203, 'p99_ms': 6.6519, 'p99.9_ms': 6.8485, 'max_cum_ms': 9.3175}
W t_admit_ns: {'n': 13484, 'mean_ms': 0.1043, 'p50_ms': 0.0906, 'p90_ms': 0.1101, 'p99_ms': 0.8561, 'p99.9_ms': 1.0281, 'max_cum_ms': 6.6699}
W release_processing_ns: {'n': 2061638, 'mean_ms': 0.0653, 'p50_ms': 0.0648, 'p90_ms': 0.0824, 'p99_ms': 0.106, 'p99.9_ms': 0.1423, 'max_cum_ms': 1.5571}
W loop_lag_ns: {'n': 53640, 'mean_ms': 0.7065, 'p50_ms': 0.008, 'p90_ms': 1.7285, 'p99_ms': 6.9796, 'p99.9_ms': 9.5027, 'max_cum_ms': 20.9809}
W audit_batch_write_ns: {'n': 26725, 'mean_ms': 0.9358, 'p50_ms': 0.8806, 'p90_ms': 1.1059, 'p99_ms': 1.4664, 'p99.9_ms': 7.7005, 'max_cum_ms': 14.4012}
W counts: {"admitted": 13484, "audit_enqueued": 26733, "audit_written": 26733, "background_round_trips": 10728, "disposition_ALLOW": 13227, "disposition_BLOCK": 257, "guard_windows": 22204, "lease_refills": 187, "provider_calls": 13227, "provider_connections_opened": 1449, "requests_by_round_trips{n=\"0\"}": 13297, "requests_by_round_trips{n=\"1\"}": 187, "shared_state_round_trips": 187}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 0.054}, "per_proc_util": {"nginx": [0.0, 0.005, 0.005, 0.006, 0.007, 0.007, 0.008, 0.008, 0.008]}, "per_core_util": {"max": 0.008, "mean": 0.007, "sum": 0.06, "n": 8}, "nic": null, "restarted": [], "loadavg_max": 0.0} nginx cpu-ms/req 1.212
redis: ops/s 310.9 ops/req 6.909 cpu cores 0.003 clients 91 mem 279.2MB ping(us) {'n': 28485, 'p50_us': 435.7, 'p90_us': 461.0, 'p99_us': 528.4, 'p99.9_us': 1132.6, 'max_us': 3959.9, 'mean_us': 440.7}
redis cmdstats: {'get': {'calls_per_s': 0.6, 'usec_per_call': 0.65}, 'info': {'calls_per_s': 0.0, 'usec_per_call': 125.0}, 'xadd': {'calls_per_s': 89.1, 'usec_per_call': 2.81}, 'hgetall': {'calls_per_s': 89.4, 'usec_per_call': 0.22}, 'ping': {'calls_per_s': 94.8, 'usec_per_call': 0.1}, 'decrby': {'calls_per_s': 0.6, 'usec_per_call': 0.47}, 'mget': {'calls_per_s': 35.8, 'usec_per_call': 0.4}, 'evalsha': {'calls_per_s': 0.6, 'usec_per_call': 13.92}}
wire: {'requests_in_window': 13500, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56140.6, 'client_side_to_unit': 9521.6, 'unit_to_provider': 10207.7, 'provider_to_unit': 56823.1, 'unit_to_redis': 5766.4, 'redis_to_unit': 483.7}, 'edge_ip_bytes_per_req': {'edge_to_clients': 56260.9, 'clients_to_edge': 8171.5}, 'olg_resp_body_bytes_mean': {'sse': 67578.1, 'json': 1427.4}}
