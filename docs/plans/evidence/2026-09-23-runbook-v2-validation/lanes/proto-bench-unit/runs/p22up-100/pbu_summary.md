# p22up-100: strict FAIL | load-knee PASS (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 30299 (101.0/s) qualified 29788 (99.29/s) FP-blocks 511 (0.01687) expected-blocks 0 infra 0 (0.0) drops 0 safety 0 detection-misses 0
by class: {'benign': {'policy_block_fp': 511, 'qualified': 29788}}
infra reasons: {}
infra detail: {}

T_fw_addon: n=29788 p50=10.4451 p90=17.6991 p99=54.3669 p99.9=71.598 max=80.0453 mean=13.0615
T_fw_addon_nohold: n=29788 p50=10.1576 p90=14.0277 p99=18.8499 p99.9=24.121 max=32.0986 mean=9.9197
T_fw_addon_sse: n=20875 p50=10.5709 p90=33.7273 p99=56.6094 p99.9=72.8767 max=80.0453 mean=14.3788
T_fw_addon_json: n=8913 p50=10.1943 p90=14.1252 p99=18.8374 p99.9=24.1482 max=27.6667 mean=9.9762
T_addon_first_sse: n=20875 p50=10.0749 p90=14.1455 p99=28.9099 p99.9=34.6896 max=52.5505 mean=10.1093
T_addon_total_sse: n=20875 p50=10.1434 p90=13.9958 p99=18.8521 p99.9=24.1121 max=32.0986 mean=9.8956
T_addon_total_json: n=8913 p50=10.1943 p90=14.1252 p99=18.8374 p99.9=24.1482 max=27.6667 mean=9.9762
T_release_lag_max: n=2128 p50=49.9513 p90=56.4522 p99=72.4252 p99.9=76.1367 max=80.0453 mean=50.0468
client_ttft_sse: n=20875 p50=160.121 p90=164.195 p99=178.9834 p99.9=184.7515 max=202.5988 mean=160.1558
lateness: n=30299 p50=0.083 p90=0.0933 p99=0.1054 p99.9=0.1308 max=0.336 mean=0.0827
loadgen: [{'vm': 'rv-pbu-lg-1', 'busy_max': 16.8, 'busy_mean': 5.8, 'late_max_us': 336, 'conn_opens': 307, 'max_inflight': 284}, {'vm': 'rv-pbu-lg-2', 'busy_max': 18.2, 'busy_mean': 5.5, 'late_max_us': 331, 'conn_opens': 322, 'max_inflight': 285}]
wire per client request: {'client_to_gw': 8398.7, 'gw_to_client': 56584.1, 'gw_to_provider': 9187.8, 'provider_to_gw': 57187.8}
gateway cores 3.576 cpu-ms/req 35.52 (workers 31.466, owners 4.049, redis 0.178)
worker util {'n': 18, 'min': 0.13, 'median': 0.18, 'max': 0.229}; per-core schedstat max 0.171 mean 0.152; procstat max 0.308
gpu: {'0': {'samples': 298, 'sm_mean': 15.8, 'sm_max': 37.0, 'mem_mean': 2.8, 'fb_mb_max': 434.0}, '1': {'samples': 298, 'sm_mean': 14.6, 'sm_max': 37.0, 'mem_mean': 2.7, 'fb_mb_max': 434.0}}
rss max total by role (MB): {'redis': 857.8, 'launcher': 59.8, 'owner': 3064.2, 'worker': 3656.7}; fds {'redis': 0, 'launcher': 7, 'owner': 131, 'worker': 1534}
W t_input_ns: {'n': 30264, 'mean_ms': 8.4555, 'p50_ms': 8.8474, 'p90_ms': 12.5174, 'p99_ms': 16.4495, 'p99.9_ms': 20.3162, 'max_cum_ms': 35.0298}
W t_admit_ns: {'n': 30264, 'mean_ms': 0.0925, 'p50_ms': 0.0886, 'p90_ms': 0.1101, 'p99_ms': 0.1423, 'p99.9_ms': 0.6185, 'max_cum_ms': 6.231}
W t_tokenize_ns: {'n': 30264, 'mean_ms': 3.0983, 'p50_ms': 3.0966, 'p90_ms': 4.8169, 'p99_ms': 5.7999, 'p99.9_ms': 6.8485, 'max_cum_ms': 7.98}
W t_det_scan_ns: {'n': 30264, 'mean_ms': 0.1092, 'p50_ms': 0.106, 'p90_ms': 0.1464, 'p99_ms': 0.1915, 'p99.9_ms': 0.2263, 'max_cum_ms': 3.4273}
W t_guard_wait_ns: {'n': 30264, 'mean_ms': 4.7704, 'p50_ms': 4.8824, 'p90_ms': 7.1762, 'p99_ms': 11.3377, 'p99.9_ms': 15.401, 'max_cum_ms': 29.0938}
W guard_owner_rtt_ns: {'n': 30264, 'mean_ms': 4.9428, 'p50_ms': 5.1446, 'p90_ms': 7.4383, 'p99_ms': 11.2067, 'p99.9_ms': 15.1388, 'max_cum_ms': 23.6071}
W guard_queue_ns: {'n': 30264, 'mean_ms': 0.5523, 'p50_ms': 0.1132, 'p90_ms': 1.9579, 'p99_ms': 5.931, 'p99.9_ms': 9.2406, 'max_cum_ms': 15.9047}
W guard_exec_ns: {'n': 30264, 'mean_ms': 3.5967, 'p50_ms': 4.2926, 'p90_ms': 4.5548, 'p99_ms': 6.5864, 'p99.9_ms': 6.8485, 'max_cum_ms': 8.0825}
W dispatch_headers_ns: {'n': 29760, 'mean_ms': 1512.8949, 'p50_ms': 149.9464, 'p90_ms': 5872.0256, 'p99_ms': 7952.4004, 'p99.9_ms': 8153.727, 'max_cum_ms': 8136.8244}
W release_lag_ns: {'n': 4642586, 'mean_ms': 19.8894, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 100.1049}
W holdback_wait_ns: {'n': 4524662, 'mean_ms': 20.3415, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 100.0078}
W release_processing_ns: {'n': 4642586, 'mean_ms': 0.0646, 'p50_ms': 0.0632, 'p90_ms': 0.0835, 'p99_ms': 0.1101, 'p99.9_ms': 0.1403, 'max_cum_ms': 3.7107}
W t_finalize_ns: {'n': 29764, 'mean_ms': 0.2597, 'p50_ms': 0.255, 'p90_ms': 0.3215, 'p99_ms': 0.383, 'p99.9_ms': 0.4321, 'max_cum_ms': 3.0203}
W loop_lag_ns: {'n': 53520, 'mean_ms': 0.8299, 'p50_ms': 0.0099, 'p90_ms': 3.0638, 'p99_ms': 8.5852, 'p99.9_ms': 11.862, 'max_cum_ms': 39.2367}
W guard_windows_per_request: {'n': 30264, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_exec_ns: {'n': 30258, 'mean_ms': 3.5965, 'p50_ms': 4.2926, 'p90_ms': 4.5548, 'p99_ms': 6.5864, 'p99.9_ms': 6.8485, 'max_cum_ms': 8.0825}
O guard_batch_windows: {'n': 30258, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_queue_ns: {'n': 30258, 'mean_ms': 0.5523, 'p50_ms': 0.1132, 'p90_ms': 1.9579, 'p99_ms': 5.931, 'p99.9_ms': 9.2406, 'max_cum_ms': 15.9047}
worker counts: {'admitted': 30264, 'audit_enqueued': 60028, 'audit_written': 60027, 'background_round_trips': 10704, 'disposition_ALLOW': 29753, 'disposition_BLOCK': 511, 'guard_windows': 49861, 'lease_granted_tokens{org="org-a"}': 28761600, 'lease_refills': 140, 'provider_calls': 29753, 'provider_connections_opened': 4469, 'quota_admitted_tokens{org="org-a"}': 28682613, 'requests_by_round_trips{n="0"}': 30124, 'requests_by_round_trips{n="1"}': 140, 'shared_state_round_trips': 140}
owner counts: {'guard_batches': 30258, 'guard_windows': 49848, 'owner_requests': 30258, 'owner_windows': 49848}
gauges: {'audit_queue_bound': [181], 'audit_completeness_ratio': [0.9999504189597898, 1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'guard_tokens_per_s': [25883.361277508728, 26080.30872146161], 'input_queue_cap': [68.0], 'guard_queue_cap_tokens': [25883.0, 26080.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 232950, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 232950.25149757855}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 234722, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 234722.77849315447}]
notes: []
