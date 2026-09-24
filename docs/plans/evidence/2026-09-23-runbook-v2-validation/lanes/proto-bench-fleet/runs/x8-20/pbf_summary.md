# x8-20: strict FAIL | load-knee PASS (sut, units=1, rate=20)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 1200 (20.0/s) qualified 1186 (19.77/s) FP-blocks 14 (0.01167) infra 0 (0.0) drops 0 safety 0
infra reasons: {}
infra detail: {}

T_fw_addon: n=1186 p50=10.3335 p90=14.4726 p99=53.9668 p99.9=71.8324 max=91.5064 mean=12.4912
T_fw_addon_nohold: n=1186 p50=10.139 p90=13.0216 p99=15.6318 p99.9=17.8465 max=20.2414 mean=9.6191
T_fw_addon_sse: n=828 p50=10.3865 p90=30.9359 p99=54.2156 p99.9=91.5064 max=91.5064 mean=13.7384
T_fw_addon_json: n=358 p50=9.9179 p90=13.2039 p99=16.1216 p99.9=20.2414 max=20.2414 mean=9.6065
T_addon_first_sse: n=828 p50=10.1055 p90=12.6586 p99=15.814 p99.9=31.1275 max=31.1275 mean=9.6217
T_addon_total_sse: n=828 p50=10.1683 p90=12.99 p99=15.4134 p99.9=17.8465 max=17.8465 mean=9.6245
T_addon_total_json: n=358 p50=9.9179 p90=13.2039 p99=16.1216 p99.9=20.2414 max=20.2414 mean=9.6065
T_release_lag_max: n=83 p50=49.7339 p90=54.2156 p99=91.5064 p99.9=91.5064 max=91.5064 mean=49.6905
lateness: n=1200 p50=0.0955 p90=0.1056 p99=0.1156 p99.9=0.1271 max=0.1286 mean=0.0954
loadgen: [{'vm': 'rv-pbf-lg-4', 'busy_max': 4.2, 'busy_mean': 3.9, 'late_max_us': 358, 'conn_opens': 108, 'max_inflight': 108}]  provider_cpu_busy_max: 17.403314780121647
gateway cores total 0.71 cpu-ms/req {'gateway': 36.272, 'workers': 32.081, 'owners': 4.175}
  rv-pbf-unit-6: cores {'launcher': 0.0, 'owner': 0.082, 'owner0': 0.082, 'redis': 0.001, 'worker': 0.631} worker util max 0.134 per-core max 0.101 mean 0.093 gpu {'0': {'n': 61, 'sm_mean': 6.1, 'sm_p95': 9.0, 'sm_max': 9.0}} t_input_p99 12.5174 per-worker admitted {'n': 6, 'min': 156, 'max': 250, 'mean': 200.2, 'max_over_mean': 1.249, 'sheds_per_worker': [0, 0, 0, 0, 0, 0]}
W t_input_ns: {'n': 1201, 'mean_ms': 7.5189, 'p50_ms': 8.2248, 'p90_ms': 10.1581, 'p99_ms': 12.5174, 'p99.9_ms': 14.7456, 'max_cum_ms': 30.0859}
W t_tokenize_ns: {'n': 1201, 'mean_ms': 2.6908, 'p50_ms': 2.7034, 'p90_ms': 4.1452, 'p99_ms': 4.6203, 'p99.9_ms': 6.1276, 'max_cum_ms': 6.6898}
W t_guard_wait_ns: {'n': 1201, 'mean_ms': 4.2686, 'p50_ms': 4.8824, 'p90_ms': 5.3412, 'p99_ms': 7.3728, 'p99.9_ms': 9.6338, 'max_cum_ms': 12.68}
W guard_owner_rtt_ns: {'n': 1201, 'mean_ms': 4.4415, 'p50_ms': 5.079, 'p90_ms': 5.5378, 'p99_ms': 7.6349, 'p99.9_ms': 9.2406, 'max_cum_ms': 16.3363}
W guard_queue_ns: {'n': 1201, 'mean_ms': 0.1147, 'p50_ms': 0.1132, 'p90_ms': 0.1321, 'p99_ms': 0.1587, 'p99.9_ms': 0.1915, 'max_cum_ms': 0.9123}
W guard_exec_ns: {'n': 1201, 'mean_ms': 3.6163, 'p50_ms': 4.3581, 'p90_ms': 4.5548, 'p99_ms': 6.6519, 'p99.9_ms': 6.783, 'max_cum_ms': 9.3512}
W t_admit_ns: {'n': 1201, 'mean_ms': 0.1036, 'p50_ms': 0.0886, 'p90_ms': 0.1091, 'p99_ms': 0.8724, 'p99.9_ms': 1.3025, 'max_cum_ms': 7.9768}
W release_processing_ns: {'n': 187574, 'mean_ms': 0.0631, 'p50_ms': 0.0627, 'p90_ms': 0.0804, 'p99_ms': 0.108, 'p99.9_ms': 0.1546, 'max_cum_ms': 0.337}
W loop_lag_ns: {'n': 3580, 'mean_ms': 0.6668, 'p50_ms': 0.0066, 'p90_ms': 1.5155, 'p99_ms': 6.783, 'p99.9_ms': 7.9626, 'max_cum_ms': 11.0249}
W audit_batch_write_ns: {'n': 2393, 'mean_ms': 1.0881, 'p50_ms': 1.0363, 'p90_ms': 1.3517, 'p99_ms': 1.7121, 'p99.9_ms': 7.5039, 'max_cum_ms': 8.7065}
W counts: {"admitted": 1201, "audit_enqueued": 2394, "audit_written": 2394, "background_round_trips": 716, "disposition_ALLOW": 1186, "disposition_BLOCK": 15, "guard_windows": 1954, "lease_refills": 15, "provider_calls": 1186, "provider_connections_opened": 137, "requests_by_round_trips{n=\"0\"}": 1186, "requests_by_round_trips{n=\"1\"}": 15, "shared_state_round_trips": 15}
edge: null nginx cpu-ms/req None
redis: ops/s 196.8 ops/req 9.839 cpu cores 0.002 clients 37 mem 15.0MB ping(us) {'n': 5661, 'p50_us': 493.8, 'p90_us': 568.9, 'p99_us': 706.3, 'p99.9_us': 835.9, 'max_us': 1219.2, 'mean_us': 507.7}
redis cmdstats: {'get': {'calls_per_s': 0.2, 'usec_per_call': 1.13}, 'info': {'calls_per_s': 0.0, 'usec_per_call': 152.0}, 'xadd': {'calls_per_s': 39.6, 'usec_per_call': 3.31}, 'hgetall': {'calls_per_s': 44.9, 'usec_per_call': 0.25}, 'hello': {'calls_per_s': 0.1, 'usec_per_call': 4.33}, 'ping': {'calls_per_s': 93.5, 'usec_per_call': 0.11}, 'decrby': {'calls_per_s': 0.2, 'usec_per_call': 0.6}, 'mget': {'calls_per_s': 17.9, 'usec_per_call': 0.48}, 'evalsha': {'calls_per_s': 0.2, 'usec_per_call': 21.4}}
wire: {'requests_in_window': 1200, 'unit_ip_bytes_per_req': {'unit_to_client_side': 57418.8, 'client_side_to_unit': 8462.4, 'unit_to_provider': 9392.7, 'provider_to_unit': 58129.4, 'unit_to_redis': 5843.3, 'redis_to_unit': 608.6}, 'olg_resp_body_bytes_mean': {'sse': 68851.4, 'json': 1414.6}}
