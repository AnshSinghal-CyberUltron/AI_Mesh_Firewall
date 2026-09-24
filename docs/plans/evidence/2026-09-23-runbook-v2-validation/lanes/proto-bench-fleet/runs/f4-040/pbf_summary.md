# f4-040: strict FAIL | load-knee FAIL (sut, units=1, rate=40)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 12000 (40.0/s) qualified 11784 (39.28/s) FP-blocks 214 (0.01783) infra 2 (0.00016666666666666666) drops 0 safety 0
infra reasons: {'block_on_unavailable_sem': 1, 'http_503': 1, 'incomplete': 1, 'unjoined': 1, 'disposition_missing': 1, 'stage_canon_missing': 1, 'stage_det_missing': 1, 'stage_sem_missing': 1, 'stage_resolve_missing': 1, 'stage_dispatch_missing': 1, 'stage_out_missing': 1, 'stage_audit_missing': 1}
infra detail: {'403 http_403 type=policy_violation code=blocked_by_policy': 1, '503 http_503 type=server_overloaded code=overloaded': 1}

T_fw_addon: n=11784 p50=11.2105 p90=19.0956 p99=56.413 p99.9=79.6752 max=144.5953 mean=14.1026
T_fw_addon_nohold: n=11784 p50=10.8403 p90=14.8349 p99=21.2025 p99.9=39.5064 max=124.5195 mean=10.8403
T_fw_addon_sse: n=8248 p50=11.3583 p90=39.4943 p99=59.4373 p99.9=82.1348 max=144.5953 mean=15.4902
T_fw_addon_json: n=3536 p50=10.8393 p90=14.8505 p99=21.0352 p99.9=46.7705 max=124.5195 mean=10.8662
T_addon_first_sse: n=8248 p50=10.6911 p90=14.6453 p99=30.6273 p99.9=39.4734 max=123.3121 mean=10.871
T_addon_total_sse: n=8248 p50=10.8405 p90=14.8339 p99=21.2743 p99.9=28.9572 max=110.4928 mean=10.8292
T_addon_total_json: n=3536 p50=10.8393 p90=14.8505 p99=21.0352 p99.9=46.7705 max=124.5195 mean=10.8662
T_release_lag_max: n=861 p50=51.1087 p90=58.467 p99=78.7554 p99.9=144.5953 max=144.5953 mean=51.818
lateness: n=12000 p50=0.0828 p90=0.0926 p99=0.107 p99.9=0.1264 max=0.2178 mean=0.084
loadgen: [{'vm': 'rv-pbf-lg-4', 'busy_max': 15.3, 'busy_mean': 4.7, 'late_max_us': 481, 'conn_opens': 229, 'max_inflight': 216}]  provider_cpu_busy_max: 19.522165005756108
gateway cores total 1.33 cpu-ms/req {'gateway': 33.322, 'workers': 29.229, 'owners': 4.086}
  rv-pbf-unit-7: cores {'launcher': 0.0, 'owner': 0.163, 'owner0': 0.163, 'redis': 0.001, 'worker': 1.165} worker util max 0.428 per-core max 0.35 mean 0.346 gpu {'0': {'n': 299, 'sm_mean': 12.1, 'sm_p95': 16.0, 'sm_max': 21.0}} t_input_p99 15.532 per-worker admitted {'n': 3, 'min': 3497, 'max': 4485, 'mean': 3993.3, 'max_over_mean': 1.123, 'sheds_per_worker': [0, 0, 1]}
W t_input_ns: {'n': 11979, 'mean_ms': 8.2748, 'p50_ms': 8.7163, 'p90_ms': 11.7309, 'p99_ms': 15.532, 'p99.9_ms': 22.9376, 'max_cum_ms': 87.5892}
W t_tokenize_ns: {'n': 11980, 'mean_ms': 3.1818, 'p50_ms': 3.1621, 'p90_ms': 5.0135, 'p99_ms': 6.1932, 'p99.9_ms': 7.4383, 'max_cum_ms': 19.3577}
W t_guard_wait_ns: {'n': 11979, 'mean_ms': 4.4459, 'p50_ms': 4.948, 'p90_ms': 5.7999, 'p99_ms': 8.5852, 'p99.9_ms': 16.7117, 'max_cum_ms': 70.191}
W guard_owner_rtt_ns: {'n': 11979, 'mean_ms': 4.6141, 'p50_ms': 5.1446, 'p90_ms': 5.9965, 'p99_ms': 8.5852, 'p99.9_ms': 15.0077, 'max_cum_ms': 63.3546}
W guard_queue_ns: {'n': 11978, 'mean_ms': 0.1299, 'p50_ms': 0.1101, 'p90_ms': 0.1485, 'p99_ms': 0.6431, 'p99.9_ms': 1.8268, 'max_cum_ms': 5.5404}
W guard_exec_ns: {'n': 11978, 'mean_ms': 3.6248, 'p50_ms': 4.2926, 'p90_ms': 4.5548, 'p99_ms': 6.6519, 'p99.9_ms': 7.4383, 'max_cum_ms': 13.2049}
W t_admit_ns: {'n': 11980, 'mean_ms': 0.1117, 'p50_ms': 0.0845, 'p90_ms': 0.1111, 'p99_ms': 1.0199, 'p99.9_ms': 1.3517, 'max_cum_ms': 7.295}
W release_processing_ns: {'n': 1831589, 'mean_ms': 0.0648, 'p50_ms': 0.0637, 'p90_ms': 0.0876, 'p99_ms': 0.1152, 'p99.9_ms': 0.1505, 'max_cum_ms': 27.5335}
W loop_lag_ns: {'n': 8910, 'mean_ms': 0.9872, 'p50_ms': 0.0085, 'p90_ms': 3.8175, 'p99_ms': 10.9445, 'p99.9_ms': 13.1727, 'max_cum_ms': 73.152}
W audit_batch_write_ns: {'n': 23651, 'mean_ms': 1.2291, 'p50_ms': 1.0895, 'p90_ms': 1.5647, 'p99_ms': 4.4237, 'p99.9_ms': 12.9106, 'max_cum_ms': 109.479}
W counts: {"admitted": 11980, "audit_enqueued": 23760, "audit_written": 23760, "background_round_trips": 1782, "disposition_ALLOW": 11765, "disposition_BLOCK": 214, "guard_deadline_expired": 1, "guard_unavailable_findings": 1, "guard_windows": 19724, "lease_refills": 332, "provider_calls": 11765, "provider_connections_opened": 1067, "requests_by_round_trips{n=\"0\"}": 11648, "requests_by_round_trips{n=\"1\"}": 332, "shared_state_round_trips": 332, "shed{reason=\"guard_queue\"}": 1}
edge: null nginx cpu-ms/req None
redis: ops/s 238.9 ops/req 5.972 cpu cores 0.003 clients 46 mem 645.0MB ping(us) {'n': 28202, 'p50_us': 503.9, 'p90_us': 584.1, 'p99_us': 804.5, 'p99.9_us': 2809.1, 'max_us': 7395.9, 'mean_us': 526.6}
redis cmdstats: {'get': {'calls_per_s': 1.1, 'usec_per_call': 0.67}, 'info': {'calls_per_s': 0.0, 'usec_per_call': 137.0}, 'xadd': {'calls_per_s': 79.2, 'usec_per_call': 2.94}, 'hgetall': {'calls_per_s': 44.7, 'usec_per_call': 0.25}, 'ping': {'calls_per_s': 93.8, 'usec_per_call': 0.1}, 'decrby': {'calls_per_s': 1.1, 'usec_per_call': 0.47}, 'mget': {'calls_per_s': 17.9, 'usec_per_call': 0.43}, 'evalsha': {'calls_per_s': 1.1, 'usec_per_call': 14.14}}
wire: {'requests_in_window': 12000, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56148.3, 'client_side_to_unit': 8397.1, 'unit_to_provider': 8951.4, 'provider_to_unit': 56836.4, 'unit_to_redis': 5534.6, 'redis_to_unit': 409.0}, 'olg_resp_body_bytes_mean': {'sse': 67462.4, 'json': 1430.1}}
