# x1u-350: strict FAIL | load-knee FAIL (sut, units=1, rate=350)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': False, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 21000 (350.0/s) qualified 17996 (299.93/s) FP-blocks 307 (0.01462) infra 2697 (0.12842857142857142) drops 0 safety 0
infra reasons: {'http_503': 2697, 'incomplete': 2697, 'unjoined': 2697, 'disposition_missing': 2697, 'stage_canon_missing': 2697, 'stage_det_missing': 2697, 'stage_sem_missing': 2697, 'stage_resolve_missing': 2697, 'stage_dispatch_missing': 2697, 'stage_out_missing': 2697, 'stage_audit_missing': 2697}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 2697}

T_fw_addon: n=17996 p50=14.4166 p90=27.0088 p99=62.4429 p99.9=78.6994 max=100.6948 mean=17.5506
T_fw_addon_nohold: n=17996 p50=13.7501 p90=20.5862 p99=29.3572 p99.9=38.1952 max=55.4126 mean=14.1405
T_fw_addon_sse: n=12621 p50=14.7519 p90=42.8201 p99=65.268 p99.9=79.5655 max=100.6948 mean=18.9476
T_fw_addon_json: n=5375 p50=13.7919 p90=20.8804 p99=29.8049 p99.9=40.5543 max=42.8009 mean=14.2703
T_addon_first_sse: n=12621 p50=13.6962 p90=20.875 p99=33.1038 p99.9=44.7686 max=55.1051 mean=14.2826
T_addon_total_sse: n=12621 p50=13.7338 p90=20.4228 p99=29.0365 p99.9=37.4675 max=55.4126 mean=14.0853
T_addon_total_json: n=5375 p50=13.7919 p90=20.8804 p99=29.8049 p99.9=40.5543 max=42.8009 mean=14.2703
T_release_lag_max: n=1299 p50=54.3434 p90=65.198 p99=79.5655 p99.9=96.2472 max=100.6948 mean=55.2449
lateness: n=21000 p50=0.0864 p90=0.0956 p99=0.1061 p99.9=0.1239 max=0.1785 mean=0.0853
loadgen: [{'vm': 'rv-pbf-lg-1', 'busy_max': 8.3, 'busy_mean': 7.8, 'late_max_us': 433, 'conn_opens': 493, 'max_inflight': 493}, {'vm': 'rv-pbf-lg-2', 'busy_max': 8.1, 'busy_mean': 7.6, 'late_max_us': 211, 'conn_opens': 502, 'max_inflight': 502}, {'vm': 'rv-pbf-lg-3', 'busy_max': 8.2, 'busy_mean': 7.6, 'late_max_us': 209, 'conn_opens': 510, 'max_inflight': 510}]  provider_cpu_busy_max: 23.951714025326744
gateway cores total 10.68 cpu-ms/req {'gateway': 31.017, 'workers': 27.418, 'owners': 3.597}
  rv-pbf-unit-2: cores {'launcher': 0.001, 'owner': 1.238, 'owner0': 0.627, 'owner1': 0.611, 'redis': 0.001, 'worker': 9.439} worker util max 0.629 per-core max 0.502 mean 0.45 gpu {'0': {'n': 60, 'sm_mean': 47.2, 'sm_p95': 60.0, 'sm_max': 66.0}, '1': {'n': 60, 'sm_mean': 46.2, 'sm_p95': 60.0, 'sm_max': 65.0}} t_input_p99 23.4619
W t_input_ns: {'n': 18210, 'mean_ms': 10.8746, 'p50_ms': 10.6824, 'p90_ms': 16.4495, 'p99_ms': 23.4619, 'p99.9_ms': 29.4912, 'max_cum_ms': 35.9043}
W t_tokenize_ns: {'n': 20888, 'mean_ms': 3.6914, 'p50_ms': 3.6536, 'p90_ms': 5.9965, 'p99_ms': 6.9796, 'p99.9_ms': 7.5039, 'max_cum_ms': 8.9355}
W t_guard_wait_ns: {'n': 18256, 'mean_ms': 6.3693, 'p50_ms': 5.4723, 'p90_ms': 10.8134, 'p99_ms': 17.4326, 'p99.9_ms': 22.4133, 'max_cum_ms': 29.2545}
W guard_owner_rtt_ns: {'n': 18256, 'mean_ms': 6.4738, 'p50_ms': 5.7344, 'p90_ms': 10.6824, 'p99_ms': 16.5806, 'p99.9_ms': 20.5783, 'max_cum_ms': 27.7519}
W guard_queue_ns: {'n': 18210, 'mean_ms': 1.5071, 'p50_ms': 0.1464, 'p90_ms': 4.6858, 'p99_ms': 9.6338, 'p99.9_ms': 12.9106, 'max_cum_ms': 16.1001}
W guard_exec_ns: {'n': 18210, 'mean_ms': 3.6822, 'p50_ms': 4.3581, 'p90_ms': 4.7514, 'p99_ms': 6.914, 'p99.9_ms': 7.2417, 'max_cum_ms': 8.1548}
W t_admit_ns: {'n': 20888, 'mean_ms': 0.1031, 'p50_ms': 0.0937, 'p90_ms': 0.1203, 'p99_ms': 0.1546, 'p99.9_ms': 1.3353, 'max_cum_ms': 21.7487}
W release_processing_ns: {'n': 2787477, 'mean_ms': 0.0663, 'p50_ms': 0.0648, 'p90_ms': 0.0896, 'p99_ms': 0.1193, 'p99.9_ms': 0.1526, 'max_cum_ms': 5.7792}
W loop_lag_ns: {'n': 10660, 'mean_ms': 1.0855, 'p50_ms': 0.0255, 'p90_ms': 4.2926, 'p99_ms': 11.5999, 'p99.9_ms': 16.3185, 'max_cum_ms': 21.4172}
W audit_batch_write_ns: {'n': 35735, 'mean_ms': 2.0013, 'p50_ms': 1.2698, 'p90_ms': 3.4243, 'p99_ms': 12.3863, 'p99.9_ms': 21.6269, 'max_cum_ms': 40.9418}
W counts: {"admitted": 20888, "audit_enqueued": 36125, "audit_written": 36125, "background_round_trips": 2132, "disposition_ALLOW": 17903, "disposition_BLOCK": 307, "guard_owner_sheds": 46, "guard_windows": 29968, "lease_refills": 96, "provider_calls": 17903, "provider_connections_opened": 2936, "requests_by_round_trips{n=\"0\"}": 20792, "requests_by_round_trips{n=\"1\"}": 96, "shared_state_round_trips": 96, "shed{reason=\"guard_owner_queue\"}": 46, "shed{reason=\"guard_queue\"}": 2638}
edge: {"window_s": 61.0, "cores_by_role": {"nginx": 0.492}, "per_proc_util": {"nginx": [0.0, 0.025, 0.026, 0.027, 0.028, 0.028, 0.03, 0.03, 0.031, 0.032, 0.032, 0.032, 0.033, 0.033, 0.033, 0.035, 0.035]}, "per_core_util": {"max": 0.033, "mean": 0.031, "sum": 0.5, "n": 16}, "nic": null, "restarted": [], "loadavg_max": 0.41} nginx cpu-ms/req 1.429
redis: ops/s 1263.7 ops/req 3.611 cpu cores 0.016 clients 282 mem 276.9MB ping(us) {'n': 5638, 'p50_us': 510.4, 'p90_us': 543.6, 'p99_us': 664.2, 'p99.9_us': 3252.4, 'max_us': 4456.3, 'mean_us': 525.8}
redis cmdstats: {'info': {'calls_per_s': 0.0, 'usec_per_call': 126.0}, 'ping': {'calls_per_s': 93.2, 'usec_per_call': 0.13}, 'evalsha': {'calls_per_s': 1.6, 'usec_per_call': 10.67}, 'xadd': {'calls_per_s': 601.5, 'usec_per_call': 4.25}, 'mget': {'calls_per_s': 161.2, 'usec_per_call': 0.39}, 'hgetall': {'calls_per_s': 403.0, 'usec_per_call': 0.22}, 'get': {'calls_per_s': 1.6, 'usec_per_call': 0.64}, 'decrby': {'calls_per_s': 1.6, 'usec_per_call': 0.42}, 'hello': {'calls_per_s': 0.0, 'usec_per_call': 3.0}}
wire: {'requests_in_window': 21000, 'unit_ip_bytes_per_req': {'unit_to_client_side': 48977.3, 'client_side_to_unit': 7479.5, 'unit_to_provider': 7444.3, 'provider_to_unit': 49520.5, 'unit_to_redis': 4675.1, 'redis_to_unit': 245.0}, 'edge_ip_bytes_per_req': {'edge_to_clients': 49091.9, 'clients_to_edge': 7583.2}, 'olg_resp_body_bytes_mean': {'sse': 67076.6, 'json': 1416.1}}
