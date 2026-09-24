# x4-30: strict FAIL | load-knee PASS (sut, units=1, rate=30)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 1800 (30.0/s) qualified 1782 (29.7/s) FP-blocks 18 (0.01) infra 0 (0.0) drops 0 safety 0
infra reasons: {}
infra detail: {}

T_fw_addon: n=1782 p50=11.1106 p90=17.1991 p99=56.0326 p99.9=71.5078 max=72.3015 mean=13.6241
T_fw_addon_nohold: n=1782 p50=10.7354 p90=14.4343 p99=17.9208 p99.9=21.482 max=22.0305 mean=10.3263
T_fw_addon_sse: n=1246 p50=11.3982 p90=45.6105 p99=58.134 p99.9=71.5078 max=72.3015 mean=15.0741
T_fw_addon_json: n=536 p50=10.6235 p90=14.5673 p99=18.8708 p99.9=20.7803 max=20.7803 mean=10.2534
T_addon_first_sse: n=1246 p50=10.6884 p90=13.9039 p99=27.2722 p99.9=34.6281 max=34.956 mean=10.3235
T_addon_total_sse: n=1246 p50=10.7909 p90=14.3022 p99=17.6268 p99.9=21.482 max=22.0305 mean=10.3577
T_addon_total_json: n=536 p50=10.6235 p90=14.5673 p99=18.8708 p99.9=20.7803 max=20.7803 mean=10.2534
T_release_lag_max: n=137 p50=50.5497 p90=57.3516 p99=71.5078 p99.9=72.3015 max=72.3015 mean=50.8637
lateness: n=1800 p50=0.0881 p90=0.098 p99=0.1107 p99.9=0.1851 max=0.231 mean=0.0887
loadgen: [{'vm': 'rv-pbf-lg-4', 'busy_max': 4.5, 'busy_mean': 4.3, 'late_max_us': 267, 'conn_opens': 154, 'max_inflight': 154}]  provider_cpu_busy_max: 5.146356472913949
gateway cores total 1.02 cpu-ms/req {'gateway': 34.401, 'workers': 30.272, 'owners': 4.12}
  rv-pbf-unit-7: cores {'launcher': 0.0, 'owner': 0.122, 'owner0': 0.122, 'redis': 0.001, 'worker': 0.893} worker util max 0.306 per-core max 0.265 mean 0.261 gpu {'0': {'n': 60, 'sm_mean': 9.3, 'sm_p95': 12.0, 'sm_max': 14.0}} t_input_p99 14.0902 per-worker admitted {'n': 3, 'min': 580, 'max': 614, 'mean': 594.3, 'max_over_mean': 1.033, 'sheds_per_worker': [0, 0, 0]}
W t_input_ns: {'n': 1783, 'mean_ms': 7.9962, 'p50_ms': 8.5852, 'p90_ms': 11.3377, 'p99_ms': 14.0902, 'p99.9_ms': 17.1704, 'max_cum_ms': 28.678}
W t_tokenize_ns: {'n': 1783, 'mean_ms': 3.0953, 'p50_ms': 3.1621, 'p90_ms': 4.8824, 'p99_ms': 5.6689, 'p99.9_ms': 6.914, 'max_cum_ms': 7.5725}
W t_guard_wait_ns: {'n': 1783, 'mean_ms': 4.2939, 'p50_ms': 4.8824, 'p90_ms': 5.4723, 'p99_ms': 7.6349, 'p99.9_ms': 11.2067, 'max_cum_ms': 14.8107}
W guard_owner_rtt_ns: {'n': 1783, 'mean_ms': 4.4734, 'p50_ms': 5.079, 'p90_ms': 5.6689, 'p99_ms': 7.8971, 'p99.9_ms': 10.6824, 'max_cum_ms': 16.839}
W guard_queue_ns: {'n': 1783, 'mean_ms': 0.1246, 'p50_ms': 0.1121, 'p90_ms': 0.1464, 'p99_ms': 0.3789, 'p99.9_ms': 1.4172, 'max_cum_ms': 2.9827}
W guard_exec_ns: {'n': 1783, 'mean_ms': 3.6033, 'p50_ms': 4.2926, 'p90_ms': 4.5548, 'p99_ms': 6.6519, 'p99.9_ms': 7.1107, 'max_cum_ms': 9.0453}
W t_admit_ns: {'n': 1783, 'mean_ms': 0.1121, 'p50_ms': 0.0845, 'p90_ms': 0.1091, 'p99_ms': 1.0568, 'p99.9_ms': 1.3844, 'max_cum_ms': 6.6011}
W release_processing_ns: {'n': 277199, 'mean_ms': 0.0642, 'p50_ms': 0.0632, 'p90_ms': 0.0855, 'p99_ms': 0.1111, 'p99.9_ms': 0.1423, 'max_cum_ms': 2.3008}
W loop_lag_ns: {'n': 1770, 'mean_ms': 0.8399, 'p50_ms': 0.0076, 'p90_ms': 3.2604, 'p99_ms': 8.9784, 'p99.9_ms': 10.027, 'max_cum_ms': 11.7871}
W audit_batch_write_ns: {'n': 3535, 'mean_ms': 1.1158, 'p50_ms': 1.0363, 'p90_ms': 1.4336, 'p99_ms': 2.1135, 'p99.9_ms': 9.1095, 'max_cum_ms': 11.6698}
W counts: {"admitted": 1783, "audit_enqueued": 3541, "audit_written": 3540, "background_round_trips": 354, "disposition_ALLOW": 1766, "disposition_BLOCK": 17, "guard_windows": 2924, "lease_refills": 49, "provider_calls": 1766, "provider_connections_opened": 167, "requests_by_round_trips{n=\"0\"}": 1734, "requests_by_round_trips{n=\"1\"}": 49, "shared_state_round_trips": 49}
edge: null nginx cpu-ms/req None
redis: ops/s 217.6 ops/req 7.254 cpu cores 0.003 clients 46 mem 142.0MB ping(us) {'n': 5638, 'p50_us': 545.0, 'p90_us': 587.4, 'p99_us': 704.7, 'p99.9_us': 1437.2, 'max_us': 1849.6, 'mean_us': 545.9}
redis cmdstats: {'get': {'calls_per_s': 0.8, 'usec_per_call': 0.94}, 'info': {'calls_per_s': 0.0, 'usec_per_call': 125.0}, 'xadd': {'calls_per_s': 59.4, 'usec_per_call': 3.24}, 'hgetall': {'calls_per_s': 44.8, 'usec_per_call': 0.27}, 'ping': {'calls_per_s': 93.1, 'usec_per_call': 0.1}, 'decrby': {'calls_per_s': 0.8, 'usec_per_call': 0.54}, 'mget': {'calls_per_s': 17.9, 'usec_per_call': 0.54}, 'evalsha': {'calls_per_s': 0.8, 'usec_per_call': 20.18}}
wire: {'requests_in_window': 1800, 'unit_ip_bytes_per_req': {'unit_to_client_side': 57116.2, 'client_side_to_unit': 8425.4, 'unit_to_provider': 9275.3, 'provider_to_unit': 57823.3, 'unit_to_redis': 5596.6, 'redis_to_unit': 460.6}, 'olg_resp_body_bytes_mean': {'sse': 68205.3, 'json': 1441.8}}
