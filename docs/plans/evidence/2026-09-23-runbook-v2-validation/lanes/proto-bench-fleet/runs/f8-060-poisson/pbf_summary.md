# f8-060-poisson: strict FAIL | load-knee FAIL (sut, units=1, rate=60)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': False, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 18202 (60.67/s) qualified 16520 (55.07/s) FP-blocks 274 (0.01505) infra 1408 (0.07735413690803208) drops 0 safety 0
infra reasons: {'http_503': 1408, 'incomplete': 1408, 'unjoined': 1408, 'disposition_missing': 1408, 'stage_canon_missing': 1408, 'stage_det_missing': 1408, 'stage_sem_missing': 1408, 'stage_resolve_missing': 1408, 'stage_dispatch_missing': 1408, 'stage_out_missing': 1408, 'stage_audit_missing': 1408}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 1408}

T_fw_addon: n=16520 p50=11.4895 p90=19.2678 p99=55.9428 p99.9=72.9615 max=89.7622 mean=14.0884
T_fw_addon_nohold: n=16520 p50=11.1319 p90=15.4005 p99=21.2099 p99.9=26.7523 max=46.1122 mean=11.0405
T_fw_addon_sse: n=11587 p50=11.6316 p90=32.7653 p99=58.5956 p99.9=74.2184 max=89.7622 mean=15.3644
T_fw_addon_json: n=4933 p50=11.1799 p90=15.4155 p99=21.1017 p99.9=27.779 max=33.6925 mean=11.0914
T_addon_first_sse: n=11587 p50=11.0538 p90=15.5416 p99=30.7401 p99.9=37.4676 max=59.5834 mean=11.2249
T_addon_total_sse: n=11587 p50=11.1172 p90=15.3995 p99=21.217 p99.9=25.2971 max=46.1122 mean=11.0188
T_addon_total_json: n=4933 p50=11.1799 p90=15.4155 p99=21.1017 p99.9=27.779 max=33.6925 mean=11.0914
T_release_lag_max: n=1122 p50=51.0852 p90=58.7433 p99=74.2184 p99.9=86.5129 max=89.7622 mean=51.3154
lateness: n=18202 p50=0.0856 p90=0.0955 p99=0.1066 p99.9=0.1244 max=0.209 mean=0.085
loadgen: [{'vm': 'rv-pbf-lg-4', 'busy_max': 16.6, 'busy_mean': 5.5, 'late_max_us': 208, 'conn_opens': 338, 'max_inflight': 313}]  provider_cpu_busy_max: 19.23707597055686
gateway cores total 1.95 cpu-ms/req {'gateway': 32.214, 'workers': 28.456, 'owners': 3.753}
  rv-pbf-unit-6: cores {'launcher': 0.0, 'owner': 0.227, 'owner0': 0.227, 'redis': 0.001, 'worker': 1.721} worker util max 0.321 per-core max 0.258 mean 0.251 gpu {'0': {'n': 299, 'sm_mean': 17.0, 'sm_p95': 30.0, 'sm_max': 42.0}} t_input_p99 17.4326 per-worker admitted {'n': 6, 'min': 2510, 'max': 3507, 'mean': 3031.5, 'max_over_mean': 1.157, 'sheds_per_worker': [161, 165, 233, 261, 290, 297]}
W t_input_ns: {'n': 16782, 'mean_ms': 8.6885, 'p50_ms': 8.9784, 'p90_ms': 12.7795, 'p99_ms': 17.4326, 'p99.9_ms': 21.3647, 'max_cum_ms': 44.8173}
W t_tokenize_ns: {'n': 18189, 'mean_ms': 3.187, 'p50_ms': 3.1621, 'p90_ms': 5.079, 'p99_ms': 6.1276, 'p99.9_ms': 6.9796, 'max_cum_ms': 8.9682}
W t_guard_wait_ns: {'n': 16782, 'mean_ms': 4.8567, 'p50_ms': 4.948, 'p90_ms': 7.3073, 'p99_ms': 11.9931, 'p99.9_ms': 15.7942, 'max_cum_ms': 38.8422}
W guard_owner_rtt_ns: {'n': 16782, 'mean_ms': 5.0221, 'p50_ms': 5.1446, 'p90_ms': 7.5039, 'p99_ms': 11.7309, 'p99.9_ms': 15.0077, 'max_cum_ms': 38.9155}
W guard_queue_ns: {'n': 16782, 'mean_ms': 0.506, 'p50_ms': 0.1142, 'p90_ms': 1.7285, 'p99_ms': 5.6689, 'p99.9_ms': 8.5852, 'max_cum_ms': 10.8147}
W guard_exec_ns: {'n': 16782, 'mean_ms': 3.6087, 'p50_ms': 4.2926, 'p90_ms': 4.5548, 'p99_ms': 6.6519, 'p99.9_ms': 6.8485, 'max_cum_ms': 9.3512}
W t_admit_ns: {'n': 18189, 'mean_ms': 0.1018, 'p50_ms': 0.0865, 'p90_ms': 0.1121, 'p99_ms': 0.8806, 'p99.9_ms': 1.2861, 'max_cum_ms': 12.8768}
W release_processing_ns: {'n': 2579426, 'mean_ms': 0.0643, 'p50_ms': 0.0632, 'p90_ms': 0.0855, 'p99_ms': 0.1142, 'p99.9_ms': 0.1464, 'max_cum_ms': 30.7951}
W loop_lag_ns: {'n': 17840, 'mean_ms': 0.9356, 'p50_ms': 0.0098, 'p90_ms': 3.883, 'p99_ms': 10.1581, 'p99.9_ms': 13.0417, 'max_cum_ms': 41.882}
W audit_batch_write_ns: {'n': 33236, 'mean_ms': 1.1924, 'p50_ms': 0.9953, 'p90_ms': 1.3681, 'p99_ms': 6.6519, 'p99.9_ms': 12.5174, 'max_cum_ms': 43.4425}
W counts: {"admitted": 18189, "audit_enqueued": 33319, "audit_written": 33319, "background_round_trips": 3568, "disposition_ALLOW": 16508, "disposition_BLOCK": 274, "guard_windows": 27618, "lease_refills": 251, "provider_calls": 16508, "provider_connections_opened": 1741, "requests_by_round_trips{n=\"0\"}": 17938, "requests_by_round_trips{n=\"1\"}": 251, "shared_state_round_trips": 251, "shed{reason=\"guard_queue\"}": 1407}
edge: null nginx cpu-ms/req None
redis: ops/s 269.6 ops/req 4.443 cpu cores 0.003 clients 47 mem 1146.3MB ping(us) {'n': 28114, 'p50_us': 568.2, 'p90_us': 594.6, 'p99_us': 698.9, 'p99.9_us': 2208.7, 'max_us': 5916.6, 'mean_us': 572.9}
redis cmdstats: {'get': {'calls_per_s': 0.8, 'usec_per_call': 0.58}, 'info': {'calls_per_s': 0.0, 'usec_per_call': 123.0}, 'xadd': {'calls_per_s': 111.0, 'usec_per_call': 3.1}, 'hgetall': {'calls_per_s': 44.6, 'usec_per_call': 0.24}, 'hello': {'calls_per_s': 0.0, 'usec_per_call': 3.0}, 'ping': {'calls_per_s': 93.5, 'usec_per_call': 0.11}, 'decrby': {'calls_per_s': 0.8, 'usec_per_call': 0.43}, 'mget': {'calls_per_s': 17.9, 'usec_per_call': 0.44}, 'evalsha': {'calls_per_s': 0.8, 'usec_per_call': 11.63}}
wire: {'requests_in_window': 18202, 'unit_ip_bytes_per_req': {'unit_to_client_side': 52132.0, 'client_side_to_unit': 7914.4, 'unit_to_provider': 8391.2, 'provider_to_unit': 52737.8, 'unit_to_redis': 5106.2, 'redis_to_unit': 348.8}, 'olg_resp_body_bytes_mean': {'sse': 67522.2, 'json': 1419.4}}
