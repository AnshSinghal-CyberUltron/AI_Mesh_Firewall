# ub4-032: strict FAIL | load-knee PASS (sut, units=4, rate=32)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 9600 (32.0/s) qualified 9460 (31.53/s) FP-blocks 140 (0.01458) infra 0 (0.0) drops 0 safety 0
infra reasons: {}
infra detail: {}

T_fw_addon: n=9460 p50=46.9646 p90=53.2793 p99=71.1487 p99.9=74.8875 max=93.4515 mean=37.4815
T_fw_addon_nohold: n=9460 p50=10.3362 p90=12.9807 p99=15.2357 p99.9=19.0743 max=24.6061 mean=9.7097
T_fw_addon_sse: n=6611 p50=50.1134 p90=53.9938 p99=71.5686 p99.9=76.7994 max=93.4515 mean=49.4372
T_fw_addon_json: n=2849 p50=10.3601 p90=13.0809 p99=15.3599 p99.9=19.8171 max=24.6061 mean=9.7387
T_addon_first_sse: n=6611 p50=10.3096 p90=13.4229 p99=27.2694 p99.9=33.4921 max=51.7587 mean=9.9362
T_addon_total_sse: n=6611 p50=10.3262 p90=12.9491 p99=15.2009 p99.9=19.0642 max=20.9012 mean=9.6973
T_addon_total_json: n=2849 p50=10.3601 p90=13.0809 p99=15.3599 p99.9=19.8171 max=24.6061 mean=9.7387
T_release_lag_max: n=6611 p50=50.1134 p90=53.9938 p99=71.5686 p99.9=76.7994 max=93.4515 mean=49.4372
lateness: n=9600 p50=0.0851 p90=0.0943 p99=0.1083 p99.9=0.1255 max=0.1875 mean=0.0855
loadgen: [{'vm': 'rv-pbf-lg-1', 'busy_max': 4.2, 'busy_mean': 3.6, 'late_max_us': 294, 'conn_opens': 63, 'max_inflight': 61}, {'vm': 'rv-pbf-lg-2', 'busy_max': 3.9, 'busy_mean': 3.5, 'late_max_us': 187, 'conn_opens': 63, 'max_inflight': 61}, {'vm': 'rv-pbf-lg-3', 'busy_max': 4.1, 'busy_mean': 3.7, 'late_max_us': 230, 'conn_opens': 63, 'max_inflight': 61}]  provider_cpu_busy_max: 24.5491729699442
gateway cores total 1.76 cpu-ms/req {'gateway': 55.143, 'workers': 50.526, 'owners': 4.565}
  rv-pbf-unit-2: cores {'launcher': 0.0, 'owner': 0.036, 'owner0': 0.021, 'owner1': 0.015, 'redis': 0.001, 'worker': 0.378} worker util max 0.038 per-core max 0.031 mean 0.019 gpu {'0': {'n': 298, 'sm_mean': 1.6, 'sm_p95': 6.0, 'sm_max': 10.0}, '1': {'n': 298, 'sm_mean': 1.0, 'sm_p95': 4.0, 'sm_max': 8.0}} t_input_p99 12.1242 per-worker admitted {'n': 18, 'min': 28, 'max': 310, 'mean': 133.2, 'max_over_mean': 2.328, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
  rv-pbf-unit-3: cores {'launcher': 0.0, 'owner': 0.036, 'owner0': 0.017, 'owner1': 0.019, 'redis': 0.001, 'worker': 0.387} worker util max 0.036 per-core max 0.033 mean 0.02 gpu {'0': {'n': 298, 'sm_mean': 1.3, 'sm_p95': 4.0, 'sm_max': 7.0}, '1': {'n': 298, 'sm_mean': 1.4, 'sm_p95': 5.0, 'sm_max': 11.0}} t_input_p99 12.2552 per-worker admitted {'n': 18, 'min': 55, 'max': 292, 'mean': 133.3, 'max_over_mean': 2.191, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
  rv-pbf-unit-4: cores {'launcher': 0.0, 'owner': 0.038, 'owner0': 0.018, 'owner1': 0.019, 'redis': 0.001, 'worker': 0.468} worker util max 0.038 per-core max 0.032 mean 0.023 gpu {'0': {'n': 298, 'sm_mean': 1.2, 'sm_p95': 4.0, 'sm_max': 8.0}, '1': {'n': 298, 'sm_mean': 1.2, 'sm_p95': 4.0, 'sm_max': 10.0}} t_input_p99 12.6484 per-worker admitted {'n': 18, 'min': 40, 'max': 250, 'mean': 133.1, 'max_over_mean': 1.878, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
  rv-pbf-unit-5: cores {'launcher': 0.0, 'owner': 0.036, 'owner0': 0.019, 'owner1': 0.018, 'redis': 0.001, 'worker': 0.379} worker util max 0.029 per-core max 0.039 mean 0.019 gpu {'0': {'n': 298, 'sm_mean': 1.6, 'sm_p95': 6.0, 'sm_max': 11.0}, '1': {'n': 298, 'sm_mean': 1.2, 'sm_p95': 5.0, 'sm_max': 8.0}} t_input_p99 12.1242 per-worker admitted {'n': 18, 'min': 39, 'max': 225, 'mean': 133.1, 'max_over_mean': 1.69, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
W t_input_ns: {'n': 9587, 'mean_ms': 7.5527, 'p50_ms': 8.2903, 'p90_ms': 10.1581, 'p99_ms': 12.3863, 'p99.9_ms': 15.7942, 'max_cum_ms': 29.3969}
W t_tokenize_ns: {'n': 9588, 'mean_ms': 2.6108, 'p50_ms': 2.6706, 'p90_ms': 3.9813, 'p99_ms': 4.4237, 'p99.9_ms': 6.1932, 'max_cum_ms': 7.6241}
W t_guard_wait_ns: {'n': 9587, 'mean_ms': 4.3869, 'p50_ms': 5.0135, 'p90_ms': 5.4067, 'p99_ms': 7.5039, 'p99.9_ms': 11.3377, 'max_cum_ms': 17.712}
W guard_owner_rtt_ns: {'n': 9587, 'mean_ms': 4.5664, 'p50_ms': 5.2101, 'p90_ms': 5.6689, 'p99_ms': 7.7005, 'p99.9_ms': 10.6824, 'max_cum_ms': 17.6786}
W guard_queue_ns: {'n': 9587, 'mean_ms': 0.1177, 'p50_ms': 0.1152, 'p90_ms': 0.1362, 'p99_ms': 0.1587, 'p99.9_ms': 0.2642, 'max_cum_ms': 1.5889}
W guard_exec_ns: {'n': 9587, 'mean_ms': 3.7274, 'p50_ms': 4.3581, 'p90_ms': 4.6203, 'p99_ms': 6.7174, 'p99.9_ms': 6.8485, 'max_cum_ms': 9.296}
W t_admit_ns: {'n': 9588, 'mean_ms': 0.1007, 'p50_ms': 0.0947, 'p90_ms': 0.1101, 'p99_ms': 0.1362, 'p99.9_ms': 1.0445, 'max_cum_ms': 7.6442}
W release_processing_ns: {'n': 1477017, 'mean_ms': 0.0753, 'p50_ms': 0.0722, 'p90_ms': 0.0998, 'p99_ms': 0.1265, 'p99.9_ms': 0.1567, 'max_cum_ms': 1.4361}
W loop_lag_ns: {'n': 214600, 'mean_ms': 0.6406, 'p50_ms': 0.0653, 'p90_ms': 1.0895, 'p99_ms': 6.0621, 'p99.9_ms': 6.9796, 'max_cum_ms': 13.7738}
W audit_batch_write_ns: {'n': 19048, 'mean_ms': 0.9873, 'p50_ms': 0.938, 'p90_ms': 1.1878, 'p99_ms': 1.3353, 'p99.9_ms': 7.0451, 'max_cum_ms': 14.074}
W counts: {"admitted": 9588, "audit_enqueued": 19048, "audit_written": 19048, "background_round_trips": 42920, "disposition_ALLOW": 9447, "disposition_BLOCK": 140, "guard_windows": 15883, "lease_refills": 44, "provider_calls": 9447, "provider_connections_opened": 1116, "requests_by_round_trips{n=\"0\"}": 9544, "requests_by_round_trips{n=\"1\"}": 44, "shared_state_round_trips": 44}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 0.039}, "per_proc_util": {"nginx": [0.0, 0.001, 0.002, 0.002, 0.002, 0.002, 0.002, 0.002, 0.002, 0.003, 0.003, 0.003, 0.003, 0.003, 0.003, 0.003, 0.004]}, "per_core_util": {"max": 0.004, "mean": 0.003, "sum": 0.05, "n": 16}, "nic": null, "restarted": [], "loadavg_max": 0.11} nginx cpu-ms/req 1.218
redis: ops/s 658.7 ops/req 20.584 cpu cores 0.004 clients 361 mem 838.3MB ping(us) {'n': 28181, 'p50_us': 546.0, 'p90_us': 604.4, 'p99_us': 688.6, 'p99.9_us': 1202.3, 'max_us': 3239.4, 'mean_us': 558.3}
redis cmdstats: {'info': {'calls_per_s': 0.0, 'usec_per_call': 118.0}, 'hgetall': {'calls_per_s': 357.9, 'usec_per_call': 0.2}, 'evalsha': {'calls_per_s': 0.1, 'usec_per_call': 12.11}, 'xadd': {'calls_per_s': 63.5, 'usec_per_call': 3.51}, 'decrby': {'calls_per_s': 0.1, 'usec_per_call': 0.32}, 'mget': {'calls_per_s': 143.1, 'usec_per_call': 0.35}, 'get': {'calls_per_s': 0.1, 'usec_per_call': 0.57}, 'ping': {'calls_per_s': 93.8, 'usec_per_call': 0.09}}
wire: {'requests_in_window': 9600, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56573.1, 'client_side_to_unit': 10473.9, 'unit_to_provider': 10790.4, 'provider_to_unit': 57258.0, 'unit_to_redis': 6993.4, 'redis_to_unit': 1042.7}, 'edge_ip_bytes_per_req': {'edge_to_clients': 56696.1, 'clients_to_edge': 9472.4}, 'olg_resp_body_bytes_mean': {'sse': 67961.6, 'json': 1446.9}}
