# f1u-060: strict FAIL | load-knee PASS (sut, units=1, rate=60)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 18000 (60.0/s) qualified 17726 (59.09/s) FP-blocks 272 (0.01511) infra 2 (0.00011111111111111112) drops 0 safety 0
infra reasons: {'http_503': 1, 'incomplete': 1, 'unjoined': 1, 'disposition_missing': 1, 'stage_canon_missing': 1, 'stage_det_missing': 1, 'stage_sem_missing': 1, 'stage_resolve_missing': 1, 'stage_dispatch_missing': 1, 'stage_out_missing': 1, 'stage_audit_missing': 1, 'block_on_unavailable_sem': 1}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 1, '403 http_403 type=policy_violation code=blocked_by_policy': 1}

T_fw_addon: n=17726 p50=10.6076 p90=15.3583 p99=53.3621 p99.9=71.0149 max=76.4227 mean=12.8965
T_fw_addon_nohold: n=17726 p50=10.3618 p90=13.212 p99=15.9643 p99.9=20.0336 max=23.0942 mean=9.8023
T_fw_addon_sse: n=12400 p50=10.7086 p90=33.6747 p99=54.0891 p99.9=71.4249 max=76.4227 mean=14.2109
T_fw_addon_json: n=5326 p50=10.4102 p90=13.1819 p99=16.1465 p99.9=20.4906 max=23.0942 mean=9.8364
T_addon_first_sse: n=12400 p50=10.2705 p90=13.5254 p99=27.8791 p99.9=33.7858 max=53.5774 mean=9.9852
T_addon_total_sse: n=12400 p50=10.3434 p90=13.2204 p99=15.9111 p99.9=19.9979 max=22.7027 mean=9.7877
T_addon_total_json: n=5326 p50=10.4102 p90=13.1819 p99=16.1465 p99.9=20.4906 max=23.0942 mean=9.8364
T_release_lag_max: n=1289 p50=50.0327 p90=53.9933 p99=71.4249 p99.9=74.5563 max=76.4227 mean=49.2443
lateness: n=18000 p50=0.087 p90=0.096 p99=0.1066 p99.9=0.1251 max=0.2859 mean=0.0872
loadgen: [{'vm': 'rv-pbf-lg-1', 'busy_max': 4.2, 'busy_mean': 3.9, 'late_max_us': 266, 'conn_opens': 117, 'max_inflight': 110}, {'vm': 'rv-pbf-lg-2', 'busy_max': 4.2, 'busy_mean': 3.8, 'late_max_us': 264, 'conn_opens': 118, 'max_inflight': 110}, {'vm': 'rv-pbf-lg-3', 'busy_max': 4.2, 'busy_mean': 3.9, 'late_max_us': 285, 'conn_opens': 118, 'max_inflight': 110}]  provider_cpu_busy_max: 18.741252613117144
gateway cores total 2.14 cpu-ms/req {'gateway': 35.778, 'workers': 31.67, 'owners': 4.101}
  rv-pbf-unit-2: cores {'launcher': 0.0, 'owner': 0.245, 'owner0': 0.116, 'owner1': 0.129, 'redis': 0.001, 'worker': 1.894} worker util max 0.158 per-core max 0.123 mean 0.092 gpu {'0': {'n': 298, 'sm_mean': 8.6, 'sm_p95': 17.0, 'sm_max': 24.0}, '1': {'n': 298, 'sm_mean': 9.7, 'sm_p95': 20.0, 'sm_max': 24.0}} t_input_p99 12.7795 per-worker admitted {'n': 18, 'min': 606, 'max': 1591, 'mean': 998.5, 'max_over_mean': 1.593, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1]}
W t_input_ns: {'n': 17973, 'mean_ms': 7.5952, 'p50_ms': 8.2903, 'p90_ms': 10.2892, 'p99_ms': 12.7795, 'p99.9_ms': 16.9083, 'max_cum_ms': 35.9043}
W t_tokenize_ns: {'n': 17973, 'mean_ms': 2.8001, 'p50_ms': 2.8344, 'p90_ms': 4.3581, 'p99_ms': 4.948, 'p99.9_ms': 6.1932, 'max_cum_ms': 8.9355}
W t_guard_wait_ns: {'n': 17973, 'mean_ms': 4.2428, 'p50_ms': 4.8169, 'p90_ms': 5.2101, 'p99_ms': 7.3073, 'p99.9_ms': 11.9931, 'max_cum_ms': 29.2545}
W guard_owner_rtt_ns: {'n': 17973, 'mean_ms': 4.4188, 'p50_ms': 5.0135, 'p90_ms': 5.4067, 'p99_ms': 7.5694, 'p99.9_ms': 11.4688, 'max_cum_ms': 27.7519}
W guard_queue_ns: {'n': 17972, 'mean_ms': 0.1108, 'p50_ms': 0.108, 'p90_ms': 0.1306, 'p99_ms': 0.1567, 'p99.9_ms': 0.2181, 'max_cum_ms': 16.1001}
W guard_exec_ns: {'n': 17972, 'mean_ms': 3.6036, 'p50_ms': 4.2926, 'p90_ms': 4.4892, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 8.1548}
W t_admit_ns: {'n': 17973, 'mean_ms': 0.0956, 'p50_ms': 0.0896, 'p90_ms': 0.1101, 'p99_ms': 0.1362, 'p99.9_ms': 1.0445, 'max_cum_ms': 21.7487}
W release_processing_ns: {'n': 2741749, 'mean_ms': 0.0643, 'p50_ms': 0.0637, 'p90_ms': 0.0814, 'p99_ms': 0.1039, 'p99.9_ms': 0.1321, 'max_cum_ms': 5.7792}
W loop_lag_ns: {'n': 53580, 'mean_ms': 0.7735, 'p50_ms': 0.0075, 'p90_ms': 2.3101, 'p99_ms': 7.7005, 'p99.9_ms': 9.5027, 'max_cum_ms': 21.4172}
W audit_batch_write_ns: {'n': 35682, 'mean_ms': 1.0408, 'p50_ms': 0.9789, 'p90_ms': 1.237, 'p99_ms': 1.5811, 'p99.9_ms': 8.1592, 'max_cum_ms': 40.9418}
W counts: {"admitted": 17973, "audit_enqueued": 35700, "audit_written": 35700, "background_round_trips": 10716, "disposition_ALLOW": 17700, "disposition_BLOCK": 273, "guard_deadline_expired": 1, "guard_unavailable_findings": 1, "guard_windows": 29576, "lease_refills": 79, "provider_calls": 17700, "provider_connections_opened": 2390, "requests_by_round_trips{n=\"0\"}": 17894, "requests_by_round_trips{n=\"1\"}": 79, "shared_state_round_trips": 79, "shed{reason=\"guard_queue\"}": 1}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 0.066}, "per_proc_util": {"nginx": [0.0, 0.003, 0.003, 0.003, 0.003, 0.004, 0.004, 0.004, 0.004, 0.004, 0.004, 0.004, 0.005, 0.005, 0.005, 0.005, 0.005]}, "per_core_util": {"max": 0.005, "mean": 0.004, "sum": 0.07, "n": 16}, "nic": null, "restarted": [], "loadavg_max": 0.12} nginx cpu-ms/req 1.111
redis: ops/s 716.3 ops/req 11.938 cpu cores 0.005 clients 255 mem 583.0MB ping(us) {'n': 28283, 'p50_us': 494.5, 'p90_us': 546.8, 'p99_us': 641.2, 'p99.9_us': 2750.3, 'max_us': 5165.6, 'mean_us': 509.8}
redis cmdstats: {'info': {'calls_per_s': 0.0, 'usec_per_call': 129.0}, 'ping': {'calls_per_s': 94.1, 'usec_per_call': 0.1}, 'evalsha': {'calls_per_s': 0.3, 'usec_per_call': 11.46}, 'xadd': {'calls_per_s': 119.0, 'usec_per_call': 3.54}, 'mget': {'calls_per_s': 143.5, 'usec_per_call': 0.36}, 'hgetall': {'calls_per_s': 358.8, 'usec_per_call': 0.2}, 'get': {'calls_per_s': 0.3, 'usec_per_call': 0.61}, 'decrby': {'calls_per_s': 0.3, 'usec_per_call': 0.4}}
wire: {'requests_in_window': 18000, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56022.3, 'client_side_to_unit': 8398.7, 'unit_to_provider': 9167.2, 'provider_to_unit': 56701.3, 'unit_to_redis': 5670.9, 'redis_to_unit': 424.8}, 'edge_ip_bytes_per_req': {'edge_to_clients': 56150.5, 'clients_to_edge': 8639.0}, 'olg_resp_body_bytes_mean': {'sse': 67180.2, 'json': 1430.8}}
