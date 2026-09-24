# x4-20: strict FAIL | load-knee PASS (sut, units=1, rate=20)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 1200 (20.0/s) qualified 1186 (19.77/s) FP-blocks 14 (0.01167) infra 0 (0.0) drops 0 safety 0
infra reasons: {}
infra detail: {}

T_fw_addon: n=1186 p50=10.6155 p90=15.3789 p99=52.5101 p99.9=71.6082 max=75.3749 mean=12.6201
T_fw_addon_nohold: n=1186 p50=10.273 p90=13.6362 p99=17.4454 p99.9=23.357 max=24.899 mean=9.9897
T_fw_addon_sse: n=828 p50=10.7659 p90=26.6749 p99=54.1333 p99.9=75.3749 max=75.3749 mean=13.7476
T_fw_addon_json: n=358 p50=10.1362 p90=13.8489 p99=17.617 p99.9=20.5038 max=20.5038 mean=10.0121
T_addon_first_sse: n=828 p50=10.3247 p90=13.5811 p99=27.9592 p99.9=34.9101 max=34.9101 mean=10.1802
T_addon_total_sse: n=828 p50=10.3591 p90=13.6074 p99=16.5523 p99.9=24.899 max=24.899 mean=9.98
T_addon_total_json: n=358 p50=10.1362 p90=13.8489 p99=17.617 p99.9=20.5038 max=20.5038 mean=10.0121
T_release_lag_max: n=71 p50=50.4229 p90=54.7441 p99=75.3749 p99.9=75.3749 max=75.3749 mean=49.6873
lateness: n=1200 p50=0.0898 p90=0.0982 p99=0.1115 p99.9=0.1195 max=0.1248 mean=0.0901
loadgen: [{'vm': 'rv-pbf-lg-4', 'busy_max': 4.1, 'busy_mean': 3.9, 'late_max_us': 175, 'conn_opens': 108, 'max_inflight': 108}]  provider_cpu_busy_max: 17.968499840738083
gateway cores total 0.69 cpu-ms/req {'gateway': 35.15, 'workers': 30.993, 'owners': 4.143}
  rv-pbf-unit-7: cores {'launcher': 0.0, 'owner': 0.082, 'owner0': 0.082, 'redis': 0.001, 'worker': 0.61} worker util max 0.225 per-core max 0.183 mean 0.179 gpu {'0': {'n': 61, 'sm_mean': 5.6, 'sm_p95': 9.0, 'sm_max': 9.0}} t_input_p99 13.566 per-worker admitted {'n': 3, 'min': 326, 'max': 434, 'mean': 396.0, 'max_over_mean': 1.096, 'sheds_per_worker': [0, 0, 0]}
W t_input_ns: {'n': 1189, 'mean_ms': 7.7289, 'p50_ms': 8.3558, 'p90_ms': 10.5513, 'p99_ms': 13.566, 'p99.9_ms': 18.4812, 'max_cum_ms': 28.678}
W t_tokenize_ns: {'n': 1188, 'mean_ms': 2.8259, 'p50_ms': 2.8344, 'p90_ms': 4.3581, 'p99_ms': 5.4723, 'p99.9_ms': 7.1762, 'max_cum_ms': 7.5725}
W t_guard_wait_ns: {'n': 1189, 'mean_ms': 4.299, 'p50_ms': 4.8824, 'p90_ms': 5.4723, 'p99_ms': 7.5694, 'p99.9_ms': 13.1727, 'max_cum_ms': 14.8107}
W guard_owner_rtt_ns: {'n': 1189, 'mean_ms': 4.4646, 'p50_ms': 5.079, 'p90_ms': 5.7344, 'p99_ms': 7.8316, 'p99.9_ms': 10.4202, 'max_cum_ms': 16.839}
W guard_queue_ns: {'n': 1189, 'mean_ms': 0.1218, 'p50_ms': 0.1132, 'p90_ms': 0.1423, 'p99_ms': 0.2806, 'p99.9_ms': 1.0199, 'max_cum_ms': 1.5349}
W guard_exec_ns: {'n': 1189, 'mean_ms': 3.585, 'p50_ms': 4.2926, 'p90_ms': 4.6203, 'p99_ms': 6.7174, 'p99.9_ms': 6.8485, 'max_cum_ms': 9.0453}
W t_admit_ns: {'n': 1188, 'mean_ms': 0.1142, 'p50_ms': 0.0896, 'p90_ms': 0.1162, 'p99_ms': 0.9708, 'p99.9_ms': 1.237, 'max_cum_ms': 6.6011}
W release_processing_ns: {'n': 185726, 'mean_ms': 0.0647, 'p50_ms': 0.0643, 'p90_ms': 0.0845, 'p99_ms': 0.1111, 'p99.9_ms': 0.1423, 'max_cum_ms': 1.7777}
W loop_lag_ns: {'n': 1770, 'mean_ms': 0.7691, 'p50_ms': 0.0056, 'p90_ms': 2.6706, 'p99_ms': 8.0937, 'p99.9_ms': 11.5999, 'max_cum_ms': 11.7871}
W audit_batch_write_ns: {'n': 2358, 'mean_ms': 1.097, 'p50_ms': 1.0117, 'p90_ms': 1.3517, 'p99_ms': 3.457, 'p99.9_ms': 7.3073, 'max_cum_ms': 10.7314}
W counts: {"admitted": 1188, "audit_enqueued": 2359, "audit_written": 2359, "background_round_trips": 354, "disposition_ALLOW": 1175, "disposition_BLOCK": 14, "guard_windows": 1929, "lease_refills": 31, "provider_calls": 1175, "provider_connections_opened": 170, "requests_by_round_trips{n=\"0\"}": 1157, "requests_by_round_trips{n=\"1\"}": 31, "shared_state_round_trips": 31}
edge: null nginx cpu-ms/req None
redis: ops/s 197.6 ops/req 9.881 cpu cores 0.002 clients 46 mem 52.2MB ping(us) {'n': 5683, 'p50_us': 443.9, 'p90_us': 538.0, 'p99_us': 619.8, 'p99.9_us': 1217.2, 'max_us': 2688.4, 'mean_us': 462.5}
redis cmdstats: {'get': {'calls_per_s': 0.5, 'usec_per_call': 0.55}, 'info': {'calls_per_s': 0.0, 'usec_per_call': 123.0}, 'xadd': {'calls_per_s': 39.6, 'usec_per_call': 2.9}, 'hgetall': {'calls_per_s': 44.6, 'usec_per_call': 0.23}, 'ping': {'calls_per_s': 93.9, 'usec_per_call': 0.11}, 'decrby': {'calls_per_s': 0.5, 'usec_per_call': 0.39}, 'mget': {'calls_per_s': 17.9, 'usec_per_call': 0.43}, 'evalsha': {'calls_per_s': 0.5, 'usec_per_call': 11.29}}
wire: {'requests_in_window': 1200, 'unit_ip_bytes_per_req': {'unit_to_client_side': 57400.6, 'client_side_to_unit': 8487.0, 'unit_to_provider': 9691.7, 'provider_to_unit': 58128.7, 'unit_to_redis': 5742.7, 'redis_to_unit': 567.7}, 'olg_resp_body_bytes_mean': {'sse': 68828.5, 'json': 1413.3}}
