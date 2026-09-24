# f4-035-r3: strict FAIL | load-knee PASS (sut, units=1, rate=35)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 10500 (35.0/s) qualified 10314 (34.38/s) FP-blocks 186 (0.01771) infra 0 (0.0) drops 0 safety 0
infra reasons: {}
infra detail: {}

T_fw_addon: n=10314 p50=10.9461 p90=17.3015 p99=55.1971 p99.9=73.3321 max=92.4836 mean=13.3809
T_fw_addon_nohold: n=10314 p50=10.6474 p90=14.2734 p99=19.2261 p99.9=25.1684 max=37.6743 mean=10.3612
T_fw_addon_sse: n=7218 p50=11.046 p90=32.2382 p99=56.5524 p99.9=74.7551 max=92.4836 mean=14.6345
T_fw_addon_json: n=3096 p50=10.7401 p90=14.4596 p99=19.1827 p99.9=26.3027 max=31.6562 mean=10.4581
T_addon_first_sse: n=7218 p50=10.4751 p90=14.2819 p99=29.1209 p99.9=34.8817 max=60.6849 mean=10.426
T_addon_total_sse: n=7218 p50=10.6093 p90=14.2318 p99=19.3979 p99.9=23.8812 max=37.6743 mean=10.3197
T_addon_total_json: n=3096 p50=10.7401 p90=14.4596 p99=19.1827 p99.9=26.3027 max=31.6562 mean=10.4581
T_release_lag_max: n=705 p50=50.4052 p90=56.5536 p99=74.7551 p99.9=92.4836 max=92.4836 mean=50.6551
lateness: n=10500 p50=0.0866 p90=0.0956 p99=0.1058 p99.9=0.1227 max=0.1514 mean=0.0855
loadgen: [{'vm': 'rv-pbf-lg-4', 'busy_max': 4.8, 'busy_mean': 4.5, 'late_max_us': 567, 'conn_opens': 203, 'max_inflight': 189}]  provider_cpu_busy_max: 16.383621373089508
gateway cores total 1.17 cpu-ms/req {'gateway': 33.655, 'workers': 29.557, 'owners': 4.09}
  rv-pbf-unit-7: cores {'launcher': 0.0, 'owner': 0.143, 'owner0': 0.143, 'redis': 0.001, 'worker': 1.031} worker util max 0.388 per-core max 0.304 mean 0.301 gpu {'0': {'n': 298, 'sm_mean': 10.5, 'sm_p95': 15.0, 'sm_max': 17.0}} t_input_p99 14.4835 per-worker admitted {'n': 3, 'min': 2672, 'max': 4024, 'mean': 3495.3, 'max_over_mean': 1.151, 'sheds_per_worker': [0, 0, 0]}
W t_input_ns: {'n': 10487, 'mean_ms': 8.0156, 'p50_ms': 8.5852, 'p90_ms': 11.4688, 'p99_ms': 14.4835, 'p99.9_ms': 20.5783, 'max_cum_ms': 87.5892}
W t_tokenize_ns: {'n': 10486, 'mean_ms': 3.0529, 'p50_ms': 2.9983, 'p90_ms': 4.948, 'p99_ms': 5.931, 'p99.9_ms': 6.3898, 'max_cum_ms': 19.3577}
W t_guard_wait_ns: {'n': 10487, 'mean_ms': 4.3528, 'p50_ms': 4.8824, 'p90_ms': 5.4723, 'p99_ms': 7.766, 'p99.9_ms': 15.6631, 'max_cum_ms': 70.191}
W guard_owner_rtt_ns: {'n': 10487, 'mean_ms': 4.5245, 'p50_ms': 5.079, 'p90_ms': 5.6689, 'p99_ms': 7.9626, 'p99.9_ms': 14.0902, 'max_cum_ms': 63.3546}
W guard_queue_ns: {'n': 10487, 'mean_ms': 0.1215, 'p50_ms': 0.1101, 'p90_ms': 0.1464, 'p99_ms': 0.3666, 'p99.9_ms': 1.2534, 'max_cum_ms': 5.5404}
W guard_exec_ns: {'n': 10487, 'mean_ms': 3.612, 'p50_ms': 4.2926, 'p90_ms': 4.4892, 'p99_ms': 6.6519, 'p99.9_ms': 6.783, 'max_cum_ms': 13.2049}
W t_admit_ns: {'n': 10486, 'mean_ms': 0.1108, 'p50_ms': 0.0824, 'p90_ms': 0.1111, 'p99_ms': 1.0035, 'p99.9_ms': 1.237, 'max_cum_ms': 10.8779}
W release_processing_ns: {'n': 1605426, 'mean_ms': 0.0645, 'p50_ms': 0.0632, 'p90_ms': 0.0865, 'p99_ms': 0.1132, 'p99.9_ms': 0.1464, 'max_cum_ms': 27.5335}
W loop_lag_ns: {'n': 8910, 'mean_ms': 0.9793, 'p50_ms': 0.0094, 'p90_ms': 3.7192, 'p99_ms': 10.9445, 'p99.9_ms': 13.9592, 'max_cum_ms': 73.152}
W audit_batch_write_ns: {'n': 20753, 'mean_ms': 1.2156, 'p50_ms': 1.0895, 'p90_ms': 1.5319, 'p99_ms': 4.8824, 'p99.9_ms': 9.2406, 'max_cum_ms': 109.479}
W counts: {"admitted": 10486, "audit_enqueued": 20811, "audit_written": 20812, "background_round_trips": 1782, "disposition_ALLOW": 10301, "disposition_BLOCK": 186, "guard_windows": 17287, "lease_refills": 290, "provider_calls": 10301, "provider_connections_opened": 979, "requests_by_round_trips{n=\"0\"}": 10196, "requests_by_round_trips{n=\"1\"}": 290, "shared_state_round_trips": 290}
edge: null nginx cpu-ms/req None
redis: ops/s 228.2 ops/req 6.52 cpu cores 0.003 clients 46 mem 1032.6MB ping(us) {'n': 28075, 'p50_us': 572.5, 'p90_us': 608.5, 'p99_us': 734.8, 'p99.9_us': 2370.8, 'max_us': 5385.7, 'mean_us': 584.4}
redis cmdstats: {'get': {'calls_per_s': 1.0, 'usec_per_call': 0.59}, 'info': {'calls_per_s': 0.0, 'usec_per_call': 122.0}, 'xadd': {'calls_per_s': 69.4, 'usec_per_call': 2.95}, 'hgetall': {'calls_per_s': 44.6, 'usec_per_call': 0.26}, 'ping': {'calls_per_s': 93.4, 'usec_per_call': 0.11}, 'decrby': {'calls_per_s': 1.0, 'usec_per_call': 0.38}, 'mget': {'calls_per_s': 17.9, 'usec_per_call': 0.46}, 'evalsha': {'calls_per_s': 1.0, 'usec_per_call': 10.7}}
wire: {'requests_in_window': 10500, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56244.8, 'client_side_to_unit': 8316.1, 'unit_to_provider': 8949.6, 'provider_to_unit': 56928.2, 'unit_to_redis': 5567.1, 'redis_to_unit': 431.4}, 'olg_resp_body_bytes_mean': {'sse': 67585.7, 'json': 1427.1}}
