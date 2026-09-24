# fleet-075-poisson: strict FAIL | load-knee FAIL (sut, units=3, rate=75)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 22806 (76.02/s) qualified 21789 (72.63/s) FP-blocks 405 (0.01776) infra 612 (0.026835043409629045) drops 0 safety 0
infra reasons: {'http_503': 612, 'incomplete': 612, 'unjoined': 612, 'disposition_missing': 612, 'stage_canon_missing': 612, 'stage_det_missing': 612, 'stage_sem_missing': 612, 'stage_resolve_missing': 612, 'stage_dispatch_missing': 612, 'stage_out_missing': 612, 'stage_audit_missing': 612}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 612}

T_fw_addon: n=21789 p50=10.9917 p90=16.6641 p99=54.1461 p99.9=71.6798 max=91.056 mean=13.3727
T_fw_addon_nohold: n=21789 p50=10.7175 p90=14.1295 p99=18.1267 p99.9=21.9846 max=35.5565 mean=10.3436
T_fw_addon_sse: n=15245 p50=11.0895 p90=32.2893 p99=55.5301 p99.9=72.7014 max=91.056 mean=14.6443
T_fw_addon_json: n=6544 p50=10.7879 p90=14.1861 p99=18.1476 p99.9=22.6681 max=31.3133 mean=10.4103
T_addon_first_sse: n=15245 p50=10.6325 p90=14.2481 p99=29.7024 p99.9=34.7598 max=54.2067 mean=10.5286
T_addon_total_sse: n=15245 p50=10.6954 p90=14.1142 p99=18.0895 p99.9=21.8866 max=35.5565 mean=10.3149
T_addon_total_json: n=6544 p50=10.7879 p90=14.1861 p99=18.1476 p99.9=22.6681 max=31.3133 mean=10.4103
T_release_lag_max: n=1522 p50=50.4956 p90=55.5301 p99=72.7014 p99.9=79.1326 max=91.056 mean=50.0948
lateness: n=22806 p50=0.0858 p90=0.0957 p99=0.1063 p99.9=0.1213 max=0.2522 mean=0.0848
loadgen: [{'vm': 'rv-pbf-lg-4', 'busy_max': 18.6, 'busy_mean': 6.5, 'late_max_us': 252, 'conn_opens': 428, 'max_inflight': 401}]  provider_cpu_busy_max: 18.864234670493275
gateway cores total 2.65 cpu-ms/req {'gateway': 35.022, 'workers': 30.945, 'owners': 4.065}
  rv-pbf-unit-6: cores {'launcher': 0.0, 'owner': 0.103, 'owner0': 0.103, 'redis': 0.001, 'worker': 0.787} worker util max 0.149 per-core max 0.122 mean 0.117 gpu {'0': {'n': 299, 'sm_mean': 7.5, 'sm_p95': 15.0, 'sm_max': 21.0}} t_input_p99 14.7456 per-worker admitted {'n': 6, 'min': 981, 'max': 1464, 'mean': 1266.7, 'max_over_mean': 1.156, 'sheds_per_worker': [22, 24, 29, 36, 36, 56]}
  rv-pbf-unit-8: cores {'launcher': 0.0, 'owner': 0.103, 'owner0': 0.103, 'redis': 0.001, 'worker': 0.781} worker util max 0.171 per-core max 0.122 mean 0.116 gpu {'0': {'n': 299, 'sm_mean': 7.1, 'sm_p95': 15.0, 'sm_max': 20.0}} t_input_p99 14.3524 per-worker admitted {'n': 6, 'min': 937, 'max': 1740, 'mean': 1266.0, 'max_over_mean': 1.374, 'sheds_per_worker': [18, 19, 24, 35, 36, 54]}
  rv-pbf-unit-9: cores {'launcher': 0.0, 'owner': 0.102, 'owner0': 0.102, 'redis': 0.001, 'worker': 0.777} worker util max 0.169 per-core max 0.119 mean 0.115 gpu {'0': {'n': 299, 'sm_mean': 7.3, 'sm_p95': 15.0, 'sm_max': 24.0}} t_input_p99 14.4835 per-worker admitted {'n': 6, 'min': 749, 'max': 1747, 'mean': 1264.5, 'max_over_mean': 1.382, 'sheds_per_worker': [9, 17, 31, 49, 58, 60]}
W t_input_ns: {'n': 22172, 'mean_ms': 7.9791, 'p50_ms': 8.5852, 'p90_ms': 11.4688, 'p99_ms': 14.4835, 'p99.9_ms': 18.219, 'max_cum_ms': 38.1453}
W t_tokenize_ns: {'n': 22784, 'mean_ms': 2.8851, 'p50_ms': 2.9, 'p90_ms': 4.4237, 'p99_ms': 5.3412, 'p99.9_ms': 6.4553, 'max_cum_ms': 8.0436}
W t_guard_wait_ns: {'n': 22172, 'mean_ms': 4.4999, 'p50_ms': 4.8824, 'p90_ms': 6.914, 'p99_ms': 9.5027, 'p99.9_ms': 13.0417, 'max_cum_ms': 25.9861}
W guard_owner_rtt_ns: {'n': 22172, 'mean_ms': 4.683, 'p50_ms': 5.079, 'p90_ms': 7.1107, 'p99_ms': 9.5027, 'p99.9_ms': 12.9106, 'max_cum_ms': 32.7484}
W guard_queue_ns: {'n': 22172, 'mean_ms': 0.264, 'p50_ms': 0.1183, 'p90_ms': 0.1628, 'p99_ms': 4.0796, 'p99.9_ms': 6.1932, 'max_cum_ms': 11.7666}
W guard_exec_ns: {'n': 22172, 'mean_ms': 3.6432, 'p50_ms': 4.2926, 'p90_ms': 4.6203, 'p99_ms': 6.7174, 'p99.9_ms': 6.914, 'max_cum_ms': 9.3175}
W t_admit_ns: {'n': 22783, 'mean_ms': 0.1044, 'p50_ms': 0.0906, 'p90_ms': 0.1121, 'p99_ms': 0.8643, 'p99.9_ms': 1.4008, 'max_cum_ms': 6.8251}
W release_processing_ns: {'n': 3388752, 'mean_ms': 0.0648, 'p50_ms': 0.0643, 'p90_ms': 0.0835, 'p99_ms': 0.108, 'p99.9_ms': 0.1403, 'max_cum_ms': 25.2296}
W loop_lag_ns: {'n': 53620, 'mean_ms': 0.7754, 'p50_ms': 0.0082, 'p90_ms': 2.6706, 'p99_ms': 7.8971, 'p99.9_ms': 10.2892, 'max_cum_ms': 26.0693}
W audit_batch_write_ns: {'n': 43877, 'mean_ms': 1.0546, 'p50_ms': 0.9216, 'p90_ms': 1.4336, 'p99_ms': 3.031, 'p99.9_ms': 8.7163, 'max_cum_ms': 28.6832}
W counts: {"admitted": 22783, "audit_enqueued": 43926, "audit_written": 43925, "background_round_trips": 10724, "disposition_ALLOW": 21767, "disposition_BLOCK": 405, "guard_windows": 36424, "lease_refills": 316, "provider_calls": 21767, "provider_connections_opened": 3033, "requests_by_round_trips{n=\"0\"}": 22467, "requests_by_round_trips{n=\"1\"}": 316, "shared_state_round_trips": 316, "shed{reason=\"guard_queue\"}": 613}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 0.095}, "per_proc_util": {"nginx": [0.0, 0.009, 0.01, 0.011, 0.012, 0.012, 0.012, 0.013, 0.015]}, "per_core_util": {"max": 0.013, "mean": 0.012, "sum": 0.1, "n": 8}, "nic": null, "restarted": [], "loadavg_max": 0.18} nginx cpu-ms/req 1.257
redis: ops/s 368.8 ops/req 4.851 cpu cores 0.004 clients 92 mem 1177.2MB ping(us) {'n': 28314, 'p50_us': 497.2, 'p90_us': 521.6, 'p99_us': 565.7, 'p99.9_us': 1446.8, 'max_us': 3677.5, 'mean_us': 501.6}
redis cmdstats: {'get': {'calls_per_s': 1.1, 'usec_per_call': 0.62}, 'info': {'calls_per_s': 0.0, 'usec_per_call': 137.0}, 'xadd': {'calls_per_s': 146.4, 'usec_per_call': 2.91}, 'hgetall': {'calls_per_s': 89.3, 'usec_per_call': 0.23}, 'ping': {'calls_per_s': 94.2, 'usec_per_call': 0.1}, 'decrby': {'calls_per_s': 1.1, 'usec_per_call': 0.41}, 'mget': {'calls_per_s': 35.7, 'usec_per_call': 0.39}, 'evalsha': {'calls_per_s': 1.1, 'usec_per_call': 11.02}}
wire: {'requests_in_window': 22806, 'unit_ip_bytes_per_req': {'unit_to_client_side': 54629.0, 'client_side_to_unit': 9269.7, 'unit_to_provider': 9877.7, 'provider_to_unit': 55283.0, 'unit_to_redis': 5460.4, 'redis_to_unit': 380.9}, 'edge_ip_bytes_per_req': {'edge_to_clients': 54741.7, 'clients_to_edge': 8061.1}, 'olg_resp_body_bytes_mean': {'sse': 67544.9, 'json': 1419.0}}
