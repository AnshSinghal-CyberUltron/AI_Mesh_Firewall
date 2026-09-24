# f4-035-r2: strict FAIL | load-knee PASS (sut, units=1, rate=35)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 10500 (35.0/s) qualified 10319 (34.4/s) FP-blocks 181 (0.01724) infra 0 (0.0) drops 0 safety 0
infra reasons: {}
infra detail: {}

T_fw_addon: n=10319 p50=10.9278 p90=17.1289 p99=54.9745 p99.9=72.4508 max=77.7732 mean=13.3774
T_fw_addon_nohold: n=10319 p50=10.6548 p90=14.2314 p99=19.3272 p99.9=24.1207 max=34.435 mean=10.3546
T_fw_addon_sse: n=7222 p50=11.02 p90=32.5115 p99=56.408 p99.9=73.0205 max=77.7732 mean=14.6344
T_fw_addon_json: n=3097 p50=10.7255 p90=14.3147 p99=19.6039 p99.9=23.6687 max=34.435 mean=10.4462
T_addon_first_sse: n=7222 p50=10.4907 p90=14.1354 p99=28.5252 p99.9=35.3299 max=54.228 mean=10.4119
T_addon_total_sse: n=7222 p50=10.6274 p90=14.1746 p99=19.0231 p99.9=24.3876 max=28.6112 mean=10.3153
T_addon_total_json: n=3097 p50=10.7255 p90=14.3147 p99=19.6039 p99.9=23.6687 max=34.435 mean=10.4462
T_release_lag_max: n=706 p50=50.5914 p90=56.6813 p99=73.0205 p99.9=77.7732 max=77.7732 mean=50.7902
lateness: n=10500 p50=0.0874 p90=0.097 p99=0.1087 p99.9=0.1274 max=0.2256 mean=0.0873
loadgen: [{'vm': 'rv-pbf-lg-4', 'busy_max': 4.8, 'busy_mean': 4.4, 'late_max_us': 410, 'conn_opens': 203, 'max_inflight': 189}]  provider_cpu_busy_max: 10.192446892254914
gateway cores total 1.18 cpu-ms/req {'gateway': 33.715, 'workers': 29.612, 'owners': 4.096}
  rv-pbf-unit-7: cores {'launcher': 0.0, 'owner': 0.143, 'owner0': 0.143, 'redis': 0.001, 'worker': 1.033} worker util max 0.373 per-core max 0.303 mean 0.301 gpu {'0': {'n': 299, 'sm_mean': 10.4, 'sm_p95': 14.0, 'sm_max': 19.0}} t_input_p99 14.2213 per-worker admitted {'n': 3, 'min': 2740, 'max': 3879, 'mean': 3494.7, 'max_over_mean': 1.11, 'sheds_per_worker': [0, 0, 0]}
W t_input_ns: {'n': 10485, 'mean_ms': 8.0185, 'p50_ms': 8.5852, 'p90_ms': 11.4688, 'p99_ms': 14.2213, 'p99.9_ms': 19.7919, 'max_cum_ms': 87.5892}
W t_tokenize_ns: {'n': 10484, 'mean_ms': 3.0599, 'p50_ms': 3.031, 'p90_ms': 4.948, 'p99_ms': 5.8655, 'p99.9_ms': 6.3898, 'max_cum_ms': 19.3577}
W t_guard_wait_ns: {'n': 10485, 'mean_ms': 4.3495, 'p50_ms': 4.8824, 'p90_ms': 5.5378, 'p99_ms': 7.766, 'p99.9_ms': 14.7456, 'max_cum_ms': 70.191}
W guard_owner_rtt_ns: {'n': 10485, 'mean_ms': 4.5234, 'p50_ms': 5.079, 'p90_ms': 5.7344, 'p99_ms': 7.9626, 'p99.9_ms': 13.4349, 'max_cum_ms': 63.3546}
W guard_queue_ns: {'n': 10485, 'mean_ms': 0.1257, 'p50_ms': 0.1111, 'p90_ms': 0.1485, 'p99_ms': 0.4608, 'p99.9_ms': 1.45, 'max_cum_ms': 5.5404}
W guard_exec_ns: {'n': 10485, 'mean_ms': 3.6164, 'p50_ms': 4.2926, 'p90_ms': 4.4892, 'p99_ms': 6.6519, 'p99.9_ms': 6.8485, 'max_cum_ms': 13.2049}
W t_admit_ns: {'n': 10484, 'mean_ms': 0.1098, 'p50_ms': 0.0835, 'p90_ms': 0.1101, 'p99_ms': 0.9789, 'p99.9_ms': 1.2206, 'max_cum_ms': 10.0802}
W release_processing_ns: {'n': 1605963, 'mean_ms': 0.0645, 'p50_ms': 0.0637, 'p90_ms': 0.0855, 'p99_ms': 0.1132, 'p99.9_ms': 0.1464, 'max_cum_ms': 27.5335}
W loop_lag_ns: {'n': 8910, 'mean_ms': 0.9765, 'p50_ms': 0.0078, 'p90_ms': 3.9813, 'p99_ms': 10.9445, 'p99.9_ms': 13.0417, 'max_cum_ms': 73.152}
W audit_batch_write_ns: {'n': 20755, 'mean_ms': 1.181, 'p50_ms': 1.0568, 'p90_ms': 1.45, 'p99_ms': 4.8824, 'p99.9_ms': 12.5174, 'max_cum_ms': 109.479}
W counts: {"admitted": 10484, "audit_enqueued": 20811, "audit_written": 20811, "background_round_trips": 1782, "disposition_ALLOW": 10304, "disposition_BLOCK": 181, "guard_windows": 17284, "lease_refills": 290, "provider_calls": 10304, "provider_connections_opened": 957, "requests_by_round_trips{n=\"0\"}": 10194, "requests_by_round_trips{n=\"1\"}": 290, "shared_state_round_trips": 290}
edge: null nginx cpu-ms/req None
redis: ops/s 229.5 ops/req 6.556 cpu cores 0.002 clients 46 mem 961.2MB ping(us) {'n': 28439, 'p50_us': 435.5, 'p90_us': 471.2, 'p99_us': 615.8, 'p99.9_us': 2220.7, 'max_us': 4062.5, 'mean_us': 449.0}
redis cmdstats: {'get': {'calls_per_s': 1.0, 'usec_per_call': 0.66}, 'info': {'calls_per_s': 0.0, 'usec_per_call': 135.0}, 'xadd': {'calls_per_s': 69.4, 'usec_per_call': 3.08}, 'hgetall': {'calls_per_s': 44.7, 'usec_per_call': 0.25}, 'ping': {'calls_per_s': 94.6, 'usec_per_call': 0.1}, 'decrby': {'calls_per_s': 1.0, 'usec_per_call': 0.46}, 'mget': {'calls_per_s': 17.9, 'usec_per_call': 0.43}, 'evalsha': {'calls_per_s': 1.0, 'usec_per_call': 12.29}}
wire: {'requests_in_window': 10500, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56275.7, 'client_side_to_unit': 8309.3, 'unit_to_provider': 8928.1, 'provider_to_unit': 56961.2, 'unit_to_redis': 5573.7, 'redis_to_unit': 433.7}, 'olg_resp_body_bytes_mean': {'sse': 67571.5, 'json': 1426.7}}
