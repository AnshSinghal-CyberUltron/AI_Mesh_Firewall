# f4-030: strict FAIL | load-knee PASS (sut, units=1, rate=30)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 9000 (30.0/s) qualified 8854 (29.51/s) FP-blocks 146 (0.01622) infra 0 (0.0) drops 0 safety 0
infra reasons: {}
infra detail: {}

T_fw_addon: n=8854 p50=11.0969 p90=17.5961 p99=55.1652 p99.9=72.9849 max=76.2068 mean=13.5675
T_fw_addon_nohold: n=8854 p50=10.7173 p90=14.2397 p99=18.914 p99.9=24.4445 max=49.4198 mean=10.3606
T_fw_addon_sse: n=6196 p50=11.2113 p90=36.2967 p99=56.4782 p99.9=73.4772 max=76.2068 mean=14.8977
T_fw_addon_json: n=2658 p50=10.774 p90=14.4202 p99=20.0647 p99.9=34.2231 max=49.4198 mean=10.4669
T_addon_first_sse: n=6196 p50=10.6142 p90=14.0149 p99=28.2247 p99.9=35.0693 max=50.3843 mean=10.382
T_addon_total_sse: n=6196 p50=10.7015 p90=14.1634 p99=18.4719 p99.9=22.7941 max=47.1898 mean=10.315
T_addon_total_json: n=2658 p50=10.774 p90=14.4202 p99=20.0647 p99.9=34.2231 max=49.4198 mean=10.4669
T_release_lag_max: n=652 p50=50.6675 p90=56.0683 p99=73.4772 p99.9=76.2068 max=76.2068 mean=50.434
lateness: n=9000 p50=0.0864 p90=0.0958 p99=0.1069 p99.9=0.1351 max=0.2412 mean=0.0863
loadgen: [{'vm': 'rv-pbf-lg-4', 'busy_max': 4.6, 'busy_mean': 4.3, 'late_max_us': 454, 'conn_opens': 167, 'max_inflight': 164}]  provider_cpu_busy_max: 17.26995995598717
gateway cores total 1.01 cpu-ms/req {'gateway': 33.668, 'workers': 29.553, 'owners': 4.106}
  rv-pbf-unit-7: cores {'launcher': 0.0, 'owner': 0.123, 'owner0': 0.123, 'redis': 0.001, 'worker': 0.884} worker util max 0.327 per-core max 0.261 mean 0.259 gpu {'0': {'n': 298, 'sm_mean': 9.0, 'sm_p95': 12.0, 'sm_max': 16.0}} t_input_p99 14.2213 per-worker admitted {'n': 3, 'min': 2491, 'max': 3369, 'mean': 2995.3, 'max_over_mean': 1.125, 'sheds_per_worker': [0, 0, 0]}
W t_input_ns: {'n': 8986, 'mean_ms': 8.0393, 'p50_ms': 8.7163, 'p90_ms': 11.2067, 'p99_ms': 14.2213, 'p99.9_ms': 19.2676, 'max_cum_ms': 42.7181}
W t_tokenize_ns: {'n': 8986, 'mean_ms': 3.1061, 'p50_ms': 3.1293, 'p90_ms': 4.8824, 'p99_ms': 5.6689, 'p99.9_ms': 6.3242, 'max_cum_ms': 14.5981}
W t_guard_wait_ns: {'n': 8986, 'mean_ms': 4.33, 'p50_ms': 4.8824, 'p90_ms': 5.4723, 'p99_ms': 7.5694, 'p99.9_ms': 13.3038, 'max_cum_ms': 27.6961}
W guard_owner_rtt_ns: {'n': 8986, 'mean_ms': 4.5012, 'p50_ms': 5.079, 'p90_ms': 5.6689, 'p99_ms': 7.766, 'p99.9_ms': 12.2552, 'max_cum_ms': 25.8446}
W guard_queue_ns: {'n': 8986, 'mean_ms': 0.1208, 'p50_ms': 0.1111, 'p90_ms': 0.1444, 'p99_ms': 0.342, 'p99.9_ms': 1.1059, 'max_cum_ms': 5.5404}
W guard_exec_ns: {'n': 8986, 'mean_ms': 3.6171, 'p50_ms': 4.2926, 'p90_ms': 4.5548, 'p99_ms': 6.6519, 'p99.9_ms': 6.8485, 'max_cum_ms': 13.2049}
W t_admit_ns: {'n': 8986, 'mean_ms': 0.1098, 'p50_ms': 0.0835, 'p90_ms': 0.1091, 'p99_ms': 0.9953, 'p99.9_ms': 1.2042, 'max_cum_ms': 7.295}
W release_processing_ns: {'n': 1371345, 'mean_ms': 0.0644, 'p50_ms': 0.0637, 'p90_ms': 0.0855, 'p99_ms': 0.1121, 'p99.9_ms': 0.1444, 'max_cum_ms': 27.5335}
W loop_lag_ns: {'n': 8910, 'mean_ms': 0.8866, 'p50_ms': 0.0081, 'p90_ms': 3.883, 'p99_ms': 9.5027, 'p99.9_ms': 11.4688, 'max_cum_ms': 15.8214}
W audit_batch_write_ns: {'n': 17797, 'mean_ms': 1.0736, 'p50_ms': 0.9871, 'p90_ms': 1.3353, 'p99_ms': 2.2774, 'p99.9_ms': 10.9445, 'max_cum_ms': 32.9038}
W counts: {"admitted": 8986, "audit_enqueued": 17835, "audit_written": 17836, "background_round_trips": 1782, "disposition_ALLOW": 8840, "disposition_BLOCK": 146, "guard_windows": 14830, "lease_refills": 248, "provider_calls": 8840, "provider_connections_opened": 775, "requests_by_round_trips{n=\"0\"}": 8738, "requests_by_round_trips{n=\"1\"}": 248, "shared_state_round_trips": 248}
edge: null nginx cpu-ms/req None
redis: ops/s 218.7 ops/req 7.289 cpu cores 0.002 clients 46 mem 492.0MB ping(us) {'n': 28289, 'p50_us': 493.7, 'p90_us': 531.0, 'p99_us': 673.7, 'p99.9_us': 1651.1, 'max_us': 5233.8, 'mean_us': 505.9}
redis cmdstats: {'get': {'calls_per_s': 0.8, 'usec_per_call': 0.71}, 'info': {'calls_per_s': 0.0, 'usec_per_call': 145.0}, 'xadd': {'calls_per_s': 59.5, 'usec_per_call': 3.26}, 'hgetall': {'calls_per_s': 44.7, 'usec_per_call': 0.27}, 'ping': {'calls_per_s': 94.1, 'usec_per_call': 0.11}, 'decrby': {'calls_per_s': 0.8, 'usec_per_call': 0.42}, 'mget': {'calls_per_s': 17.9, 'usec_per_call': 0.49}, 'evalsha': {'calls_per_s': 0.8, 'usec_per_call': 15.18}}
wire: {'requests_in_window': 9000, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56117.8, 'client_side_to_unit': 8344.5, 'unit_to_provider': 9077.1, 'provider_to_unit': 56804.3, 'unit_to_redis': 5611.9, 'redis_to_unit': 463.3}, 'olg_resp_body_bytes_mean': {'sse': 67173.2, 'json': 1427.2}}
