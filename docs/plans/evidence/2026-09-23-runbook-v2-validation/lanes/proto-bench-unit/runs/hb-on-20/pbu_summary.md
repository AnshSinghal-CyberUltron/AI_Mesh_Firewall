# hb-on-20: strict FAIL | load-knee PASS (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 12000 (40.0/s) qualified 11822 (39.41/s) FP-blocks 178 (0.01483) expected-blocks 0 infra 0 (0.0) drops 0 safety 0 detection-misses 0
by class: {'benign': {'qualified': 11822, 'policy_block_fp': 178}}
infra reasons: {}
infra detail: {}

T_fw_addon: n=11822 p50=49.3286 p90=53.478 p99=71.061 p99.9=85.9928 max=93.4984 mean=48.7811
T_fw_addon_nohold: n=11822 p50=9.5656 p90=12.3109 p99=14.7324 p99.9=18.677 max=23.9564 mean=9.0417
T_fw_addon_sse: n=11822 p50=49.3286 p90=53.478 p99=71.061 p99.9=85.9928 max=93.4984 mean=48.7811
T_fw_addon_json: n=0
T_addon_first_sse: n=11822 p50=9.4938 p90=12.7656 p99=29.2968 p99.9=33.754 max=66.3509 mean=9.2952
T_addon_total_sse: n=11822 p50=9.5656 p90=12.3109 p99=14.7324 p99.9=18.677 max=23.9564 mean=9.0417
T_addon_total_json: n=0
T_release_lag_max: n=11822 p50=49.3286 p90=53.478 p99=71.061 p99.9=85.9928 max=93.4984 mean=48.7811
client_ttft_sse: n=11822 p50=159.539 p90=162.8076 p99=179.3772 p99.9=183.8168 max=216.3822 mean=159.3428
lateness: n=12000 p50=0.0872 p90=0.097 p99=0.1085 p99.9=0.1242 max=0.3094 mean=0.0877
loadgen: [{'vm': 'rv-pbu-lg-1', 'busy_max': 18.8, 'busy_mean': 5.1, 'late_max_us': 239, 'conn_opens': 117, 'max_inflight': 110}, {'vm': 'rv-pbu-lg-2', 'busy_max': 17.9, 'busy_mean': 4.7, 'late_max_us': 309, 'conn_opens': 117, 'max_inflight': 110}]
wire per client request: {'client_to_gw': 10904.8, 'gw_to_client': 80111.6, 'gw_to_provider': 12752.7, 'provider_to_gw': 81108.0}
gateway cores 1.885 cpu-ms/req 47.281 (workers 43.113, owners 4.157, redis 0.24)
worker util {'n': 18, 'min': 0.057, 'median': 0.098, 'max': 0.134}; per-core schedstat max 0.09 mean 0.081; procstat max 0.2
gpu: {'0': {'samples': 298, 'sm_mean': 6.4, 'sm_max': 19.0, 'mem_mean': 1.2, 'fb_mb_max': 434.0}, '1': {'samples': 298, 'sm_mean': 5.8, 'sm_max': 18.0, 'mem_mean': 1.1, 'fb_mb_max': 434.0}}
rss max total by role (MB): {'redis': 204.7, 'launcher': 59.8, 'owner': 3066.8, 'worker': 3623.1}; fds {'redis': 0, 'launcher': 7, 'owner': 126, 'worker': 887}
W t_input_ns: {'n': 11995, 'mean_ms': 7.6982, 'p50_ms': 8.4541, 'p90_ms': 10.4202, 'p99_ms': 12.9106, 'p99.9_ms': 15.7942, 'max_cum_ms': 19.6548}
W t_admit_ns: {'n': 11995, 'mean_ms': 0.0917, 'p50_ms': 0.0876, 'p90_ms': 0.106, 'p99_ms': 0.1362, 'p99.9_ms': 0.6267, 'max_cum_ms': 2.9529}
W t_tokenize_ns: {'n': 11995, 'mean_ms': 2.8678, 'p50_ms': 2.9, 'p90_ms': 4.3581, 'p99_ms': 5.079, 'p99.9_ms': 6.1932, 'max_cum_ms': 7.5195}
W t_det_scan_ns: {'n': 11995, 'mean_ms': 0.1032, 'p50_ms': 0.0998, 'p90_ms': 0.1341, 'p99_ms': 0.1833, 'p99.9_ms': 0.2222, 'max_cum_ms': 1.6687}
W t_guard_wait_ns: {'n': 11995, 'mean_ms': 4.2767, 'p50_ms': 4.8824, 'p90_ms': 5.2756, 'p99_ms': 7.3728, 'p99.9_ms': 11.5999, 'max_cum_ms': 13.8031}
W guard_owner_rtt_ns: {'n': 11995, 'mean_ms': 4.4529, 'p50_ms': 5.079, 'p90_ms': 5.5378, 'p99_ms': 7.6349, 'p99.9_ms': 10.5513, 'max_cum_ms': 13.1421}
W guard_queue_ns: {'n': 11995, 'mean_ms': 0.1151, 'p50_ms': 0.1121, 'p90_ms': 0.1362, 'p99_ms': 0.1669, 'p99.9_ms': 0.2099, 'max_cum_ms': 1.5043}
W guard_exec_ns: {'n': 11995, 'mean_ms': 3.6289, 'p50_ms': 4.2926, 'p90_ms': 4.5548, 'p99_ms': 6.6519, 'p99.9_ms': 6.783, 'max_cum_ms': 9.2784}
W dispatch_headers_ns: {'n': 11819, 'mean_ms': 150.8478, 'p50_ms': 149.9464, 'p90_ms': 152.0435, 'p99_ms': 152.0435, 'p99.9_ms': 156.2378, 'max_cum_ms': 164.6999}
W release_lag_ns: {'n': 2631905, 'mean_ms': 19.886, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 80.1906}
W holdback_wait_ns: {'n': 2565124, 'mean_ms': 20.3385, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 80.1272}
W release_processing_ns: {'n': 2631905, 'mean_ms': 0.0636, 'p50_ms': 0.0627, 'p90_ms': 0.0814, 'p99_ms': 0.106, 'p99.9_ms': 0.1341, 'max_cum_ms': 1.4524}
W t_finalize_ns: {'n': 11826, 'mean_ms': 0.2652, 'p50_ms': 0.257, 'p90_ms': 0.3133, 'p99_ms': 0.3707, 'p99.9_ms': 0.4116, 'max_cum_ms': 0.531}
W loop_lag_ns: {'n': 53640, 'mean_ms': 0.6803, 'p50_ms': 0.0062, 'p90_ms': 1.7613, 'p99_ms': 6.7174, 'p99.9_ms': 8.5852, 'max_cum_ms': 11.7598}
W guard_windows_per_request: {'n': 11995, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_exec_ns: {'n': 11972, 'mean_ms': 3.6297, 'p50_ms': 4.2926, 'p90_ms': 4.5548, 'p99_ms': 6.6519, 'p99.9_ms': 6.783, 'max_cum_ms': 9.2784}
O guard_batch_windows: {'n': 11972, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_queue_ns: {'n': 11972, 'mean_ms': 0.1151, 'p50_ms': 0.1121, 'p90_ms': 0.1362, 'p99_ms': 0.1669, 'p99.9_ms': 0.2099, 'max_cum_ms': 1.5043}
worker counts: {'admitted': 11995, 'audit_enqueued': 23821, 'audit_written': 23821, 'background_round_trips': 10728, 'disposition_ALLOW': 11817, 'disposition_BLOCK': 178, 'guard_windows': 19737, 'lease_granted_tokens{org="org-a"}': 11504640, 'lease_refills': 56, 'provider_calls': 11817, 'provider_connections_opened': 2647, 'quota_admitted_tokens{org="org-a"}': 11393577, 'requests_by_round_trips{n="0"}': 11939, 'requests_by_round_trips{n="1"}': 56, 'shared_state_round_trips': 56}
owner counts: {'guard_batches': 11972, 'guard_windows': 19704, 'owner_requests': 11972, 'owner_windows': 19704}
gauges: {'audit_queue_bound': [181], 'audit_completeness_ratio': [1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'guard_tokens_per_s': [26013.467373822015, 26032.753479556235], 'input_queue_cap': [2.0], 'guard_queue_cap_tokens': [520.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4682, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 234121.20636439815}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4685, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 234294.78131600612}]
notes: []
