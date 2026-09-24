# fleet-090: strict FAIL | load-knee FAIL (sut, units=3, rate=90)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 27000 (90.0/s) qualified 26395 (87.98/s) FP-blocks 495 (0.01833) infra 110 (0.004074074074074074) drops 0 safety 0
infra reasons: {'http_503': 110, 'incomplete': 110, 'unjoined': 110, 'disposition_missing': 110, 'stage_canon_missing': 110, 'stage_det_missing': 110, 'stage_sem_missing': 110, 'stage_resolve_missing': 110, 'stage_dispatch_missing': 110, 'stage_out_missing': 110, 'stage_audit_missing': 110}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 110}

T_fw_addon: n=26395 p50=10.7821 p90=16.02 p99=53.7041 p99.9=71.0527 max=101.6421 mean=13.1071
T_fw_addon_nohold: n=26395 p50=10.4872 p90=13.6745 p99=17.0233 p99.9=21.0132 max=25.443 mean=10.0439
T_fw_addon_sse: n=18489 p50=10.8786 p90=32.859 p99=54.5757 p99.9=71.5225 max=101.6421 mean=14.3769
T_fw_addon_json: n=7906 p50=10.566 p90=13.8558 p99=16.9881 p99.9=21.466 max=23.6316 mean=10.1376
T_addon_first_sse: n=18489 p50=10.4319 p90=13.7731 p99=29.5498 p99.9=35.2721 max=55.7147 mean=10.2502
T_addon_total_sse: n=18489 p50=10.4495 p90=13.6018 p99=17.0769 p99.9=21.0132 max=25.443 mean=10.0039
T_addon_total_json: n=7906 p50=10.566 p90=13.8558 p99=16.9881 p99.9=21.466 max=23.6316 mean=10.1376
T_release_lag_max: n=1862 p50=50.2585 p90=54.5401 p99=71.5225 p99.9=75.7164 max=101.6421 mean=49.7256
lateness: n=27000 p50=0.0872 p90=0.0965 p99=0.1073 p99.9=0.125 max=0.3148 mean=0.0862
loadgen: [{'vm': 'rv-pbf-lg-4', 'busy_max': 20.6, 'busy_mean': 6.9, 'late_max_us': 773, 'conn_opens': 467, 'max_inflight': 451}]  provider_cpu_busy_max: 21.8017213743124
gateway cores total 3.15 cpu-ms/req {'gateway': 35.102, 'workers': 30.969, 'owners': 4.123}
  rv-pbf-unit-6: cores {'launcher': 0.0, 'owner': 0.123, 'owner0': 0.123, 'redis': 0.001, 'worker': 0.934} worker util max 0.174 per-core max 0.149 mean 0.138 gpu {'0': {'n': 298, 'sm_mean': 9.0, 'sm_p95': 14.0, 'sm_max': 20.0}} t_input_p99 13.566 per-worker admitted {'n': 6, 'min': 1314, 'max': 1689, 'mean': 1498.2, 'max_over_mean': 1.127, 'sheds_per_worker': [1, 3, 5, 5, 8, 9]}
  rv-pbf-unit-8: cores {'launcher': 0.0, 'owner': 0.123, 'owner0': 0.123, 'redis': 0.001, 'worker': 0.919} worker util max 0.209 per-core max 0.137 mean 0.136 gpu {'0': {'n': 298, 'sm_mean': 9.2, 'sm_p95': 15.0, 'sm_max': 20.0}} t_input_p99 13.566 per-worker admitted {'n': 6, 'min': 1031, 'max': 2116, 'mean': 1499.2, 'max_over_mean': 1.411, 'sheds_per_worker': [3, 5, 7, 7, 9, 11]}
  rv-pbf-unit-9: cores {'launcher': 0.0, 'owner': 0.124, 'owner0': 0.124, 'redis': 0.001, 'worker': 0.924} worker util max 0.183 per-core max 0.147 mean 0.137 gpu {'0': {'n': 298, 'sm_mean': 9.0, 'sm_p95': 16.0, 'sm_max': 18.0}} t_input_p99 13.4349 per-worker admitted {'n': 6, 'min': 1164, 'max': 1852, 'mean': 1499.8, 'max_over_mean': 1.235, 'sheds_per_worker': [2, 3, 4, 8, 9, 11]}
W t_input_ns: {'n': 26873, 'mean_ms': 7.7676, 'p50_ms': 8.4541, 'p90_ms': 10.5513, 'p99_ms': 13.566, 'p99.9_ms': 17.1704, 'max_cum_ms': 27.6245}
W t_tokenize_ns: {'n': 26983, 'mean_ms': 2.8778, 'p50_ms': 2.9, 'p90_ms': 4.4237, 'p99_ms': 5.2101, 'p99.9_ms': 6.3898, 'max_cum_ms': 7.2829}
W t_guard_wait_ns: {'n': 26873, 'mean_ms': 4.3094, 'p50_ms': 4.8169, 'p90_ms': 5.4067, 'p99_ms': 8.2248, 'p99.9_ms': 11.9931, 'max_cum_ms': 16.0843}
W guard_owner_rtt_ns: {'n': 26872, 'mean_ms': 4.4899, 'p50_ms': 5.079, 'p90_ms': 5.6033, 'p99_ms': 8.0282, 'p99.9_ms': 11.5999, 'max_cum_ms': 16.7258}
W guard_queue_ns: {'n': 26872, 'mean_ms': 0.1178, 'p50_ms': 0.1142, 'p90_ms': 0.1423, 'p99_ms': 0.1792, 'p99.9_ms': 0.8561, 'max_cum_ms': 4.6197}
W guard_exec_ns: {'n': 26872, 'mean_ms': 3.6254, 'p50_ms': 4.2926, 'p90_ms': 4.6203, 'p99_ms': 6.7174, 'p99.9_ms': 6.8485, 'max_cum_ms': 9.3175}
W t_admit_ns: {'n': 26983, 'mean_ms': 0.1026, 'p50_ms': 0.0886, 'p90_ms': 0.1111, 'p99_ms': 0.8397, 'p99.9_ms': 1.0281, 'max_cum_ms': 6.6699}
W release_processing_ns: {'n': 4122977, 'mean_ms': 0.0642, 'p50_ms': 0.0637, 'p90_ms': 0.0824, 'p99_ms': 0.1091, 'p99.9_ms': 0.1403, 'max_cum_ms': 1.5571}
W loop_lag_ns: {'n': 53640, 'mean_ms': 0.7392, 'p50_ms': 0.0085, 'p90_ms': 2.7689, 'p99_ms': 7.5694, 'p99.9_ms': 10.6824, 'max_cum_ms': 20.9809}
W audit_batch_write_ns: {'n': 53219, 'mean_ms': 0.9693, 'p50_ms': 0.897, 'p90_ms': 1.1223, 'p99_ms': 2.9983, 'p99.9_ms': 7.6349, 'max_cum_ms': 14.4012}
W counts: {"admitted": 26983, "audit_enqueued": 53256, "audit_written": 53255, "background_round_trips": 10728, "disposition_ALLOW": 26378, "disposition_BLOCK": 495, "guard_windows": 44118, "lease_refills": 376, "provider_calls": 26378, "provider_connections_opened": 4076, "requests_by_round_trips{n=\"0\"}": 26607, "requests_by_round_trips{n=\"1\"}": 376, "shared_state_round_trips": 376, "shed{reason=\"guard_queue\"}": 110}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 0.119}, "per_proc_util": {"nginx": [0.0, 0.013, 0.014, 0.014, 0.014, 0.015, 0.016, 0.017, 0.017]}, "per_core_util": {"max": 0.016, "mean": 0.015, "sum": 0.12, "n": 8}, "nic": null, "restarted": [], "loadavg_max": 0.19} nginx cpu-ms/req 1.325
redis: ops/s 401.0 ops/req 4.456 cpu cores 0.005 clients 91 mem 186.9MB ping(us) {'n': 28482, 'p50_us': 436.8, 'p90_us': 460.7, 'p99_us': 499.4, 'p99.9_us': 790.9, 'max_us': 3413.1, 'mean_us': 440.3}
redis cmdstats: {'get': {'calls_per_s': 1.3, 'usec_per_call': 0.57}, 'info': {'calls_per_s': 0.0, 'usec_per_call': 135.0}, 'xadd': {'calls_per_s': 177.3, 'usec_per_call': 2.73}, 'hgetall': {'calls_per_s': 89.4, 'usec_per_call': 0.24}, 'hello': {'calls_per_s': 0.0, 'usec_per_call': 4.5}, 'ping': {'calls_per_s': 94.8, 'usec_per_call': 0.11}, 'decrby': {'calls_per_s': 1.3, 'usec_per_call': 0.41}, 'mget': {'calls_per_s': 35.8, 'usec_per_call': 0.4}, 'evalsha': {'calls_per_s': 1.3, 'usec_per_call': 11.01}}
wire: {'requests_in_window': 27000, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56127.1, 'client_side_to_unit': 9218.5, 'unit_to_provider': 9920.2, 'provider_to_unit': 56814.2, 'unit_to_redis': 5544.2, 'redis_to_unit': 365.6}, 'edge_ip_bytes_per_req': {'edge_to_clients': 56241.4, 'clients_to_edge': 7936.4}, 'olg_resp_body_bytes_mean': {'sse': 67722.0, 'json': 1420.6}}
