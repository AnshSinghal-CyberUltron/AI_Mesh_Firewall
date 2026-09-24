# c22-035: strict FAIL | load-knee FAIL (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': False, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': False, 'run_valid': True}
offered 21000 (35.0/s) qualified 18809 (31.35/s) FP-blocks 137 (0.00652) expected-blocks 1463 infra 0 (0.0) drops 0 safety 591 detection-misses 0
by class: {'secret': {'policy_block_expected': 1006}, 'benign': {'qualified': 16725, 'policy_block_fp': 137}, 'pii': {'qualified': 2084}, 'injection': {'policy_block_expected': 457, 'safety_failure': 591}}
infra reasons: {}
infra detail: {}

T_fw_addon: n=18809 p50=6.1775 p90=7.6163 p99=46.4611 p99.9=65.6207 max=72.998 mean=8.3807
T_fw_addon_nohold: n=18809 p50=6.1075 p90=6.954 p99=11.4427 p99.9=13.9049 max=18.575 mean=6.2083
T_fw_addon_sse: n=13202 p50=6.1803 p90=11.6522 p99=46.6495 p99.9=65.9191 max=72.998 mean=9.2791
T_fw_addon_json: n=5607 p50=6.1713 p90=7.008 p99=11.5832 p99.9=13.8956 max=14.4272 mean=6.2653
T_addon_first_sse: n=13202 p50=5.9884 p90=6.8806 p99=25.5462 p99.9=26.9284 max=45.739 mean=6.4081
T_addon_total_sse: n=13202 p50=6.0823 p90=6.9287 p99=11.3091 p99.9=13.9049 max=18.575 mean=6.1841
T_addon_total_json: n=5607 p50=6.1713 p90=7.008 p99=11.5832 p99.9=13.8956 max=14.4272 mean=6.2653
T_release_lag_max: n=982 p50=45.7026 p90=46.829 p99=66.0079 p99.9=72.998 max=72.998 mean=43.2383
client_ttft_sse: n=13202 p50=156.0353 p90=156.9387 p99=175.6093 p99.9=177.0061 max=195.764 mean=156.4605
lateness: n=21000 p50=0.0834 p90=0.0956 p99=0.1107 p99.9=0.1357 max=0.3421 mean=0.0841
loadgen: [{'vm': 'rv-pbu-lg-1', 'busy_max': 17.0, 'busy_mean': 3.8, 'late_max_us': 342, 'conn_opens': 65, 'max_inflight': 55}, {'vm': 'rv-pbu-lg-2', 'busy_max': 16.2, 'busy_mean': 3.6, 'late_max_us': 297, 'conn_opens': 65, 'max_inflight': 55}]
wire per client request: {'client_to_gw': 4901.6, 'gw_to_client': 29461.5, 'gw_to_provider': 5065.4, 'provider_to_gw': 29666.1}
gateway cores 0.827 cpu-ms/req 23.656 (workers 20.884, owners 2.76, redis 0.231)
worker util {'n': 18, 'min': 0.023, 'median': 0.042, 'max': 0.062}; per-core schedstat max 0.049 mean 0.037; procstat max 0.075
gpu: {'0': {'samples': 595, 'sm_mean': 3.3, 'sm_max': 7.0, 'mem_mean': 0.6, 'fb_mb_max': 434.0}, '1': {'samples': 595, 'sm_mean': 3.4, 'sm_max': 9.0, 'mem_mean': 0.6, 'fb_mb_max': 434.0}}
rss max total by role (MB): {'redis': 1180.2, 'launcher': 59.7, 'owner': 3067.0, 'worker': 3661.8}; fds {'redis': 0, 'launcher': 7, 'owner': 131, 'worker': 665}
W t_input_ns: {'n': 20992, 'mean_ms': 5.0236, 'p50_ms': 4.948, 'p90_ms': 5.7344, 'p99_ms': 7.766, 'p99.9_ms': 12.3863, 'max_cum_ms': 28.6342}
W t_admit_ns: {'n': 20992, 'mean_ms': 0.0897, 'p50_ms': 0.0876, 'p90_ms': 0.0988, 'p99_ms': 0.1234, 'p99.9_ms': 0.4854, 'max_cum_ms': 3.9185}
W t_tokenize_ns: {'n': 20992, 'mean_ms': 1.6927, 'p50_ms': 1.6957, 'p90_ms': 2.4084, 'p99_ms': 2.7361, 'p99.9_ms': 3.5553, 'max_cum_ms': 8.0838}
W t_det_scan_ns: {'n': 20992, 'mean_ms': 0.0872, 'p50_ms': 0.0835, 'p90_ms': 0.1111, 'p99_ms': 0.1444, 'p99.9_ms': 0.1874, 'max_cum_ms': 3.1584}
W t_guard_wait_ns: {'n': 20992, 'mean_ms': 2.8408, 'p50_ms': 2.8017, 'p90_ms': 2.9327, 'p99_ms': 3.8502, 'p99.9_ms': 9.8959, 'max_cum_ms': 21.4965}
W guard_owner_rtt_ns: {'n': 20992, 'mean_ms': 2.9734, 'p50_ms': 2.9327, 'p90_ms': 3.0638, 'p99_ms': 3.7847, 'p99.9_ms': 9.3716, 'max_cum_ms': 18.8566}
W guard_queue_ns: {'n': 20992, 'mean_ms': 0.1044, 'p50_ms': 0.1029, 'p90_ms': 0.1224, 'p99_ms': 0.1464, 'p99.9_ms': 0.2079, 'max_cum_ms': 11.2781}
W guard_exec_ns: {'n': 20992, 'mean_ms': 2.257, 'p50_ms': 2.2446, 'p90_ms': 2.3101, 'p99_ms': 2.4084, 'p99.9_ms': 4.4237, 'max_cum_ms': 7.9147}
W dispatch_headers_ns: {'n': 19395, 'mean_ms': 886.6067, 'p50_ms': 149.9464, 'p90_ms': 3137.3394, 'p99_ms': 4076.8635, 'p99.9_ms': 4143.9724, 'max_cum_ms': 8139.2743}
W release_lag_ns: {'n': 1646909, 'mean_ms': 19.7623, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 60.031, 'max_cum_ms': 100.1673}
W holdback_wait_ns: {'n': 1591979, 'mean_ms': 20.3751, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 60.031, 'max_cum_ms': 100.0223}
W release_processing_ns: {'n': 1646909, 'mean_ms': 0.0667, 'p50_ms': 0.066, 'p90_ms': 0.0804, 'p99_ms': 0.1101, 'p99.9_ms': 0.1731, 'max_cum_ms': 3.3204}
W t_finalize_ns: {'n': 19406, 'mean_ms': 0.2486, 'p50_ms': 0.2488, 'p90_ms': 0.2888, 'p99_ms': 0.3584, 'p99.9_ms': 0.4198, 'max_cum_ms': 2.7484}
W loop_lag_ns: {'n': 107220, 'mean_ms': 0.7469, 'p50_ms': 0.0204, 'p90_ms': 1.2206, 'p99_ms': 7.1762, 'p99.9_ms': 8.3558, 'max_cum_ms': 56.9536}
W guard_windows_per_request: {'n': 20992, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_exec_ns: {'n': 21006, 'mean_ms': 2.257, 'p50_ms': 2.2446, 'p90_ms': 2.3101, 'p99_ms': 2.4084, 'p99.9_ms': 4.4237, 'max_cum_ms': 7.9147}
O guard_batch_windows: {'n': 21006, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_queue_ns: {'n': 21006, 'mean_ms': 0.1045, 'p50_ms': 0.1029, 'p90_ms': 0.1224, 'p99_ms': 0.1464, 'p99.9_ms': 0.2017, 'max_cum_ms': 11.2781}
worker counts: {'admitted': 20992, 'audit_enqueued': 40398, 'audit_written': 40398, 'background_round_trips': 21444, 'disposition_ALLOW': 17312, 'disposition_BLOCK': 1598, 'disposition_REDACT': 2082, 'guard_windows': 21092, 'lease_granted_tokens{org="org-a"}': 10682880, 'lease_refills': 52, 'provider_calls': 19394, 'provider_connections_opened': 1558, 'quota_admitted_tokens{org="org-a"}': 11062238, 'requests_by_round_trips{n="0"}': 20940, 'requests_by_round_trips{n="1"}': 52, 'shared_state_round_trips': 52}
owner counts: {'guard_batches': 21006, 'guard_windows': 21106, 'owner_requests': 21005, 'owner_windows': 21105}
gauges: {'audit_queue_bound': [181], 'audit_completeness_ratio': [1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'guard_tokens_per_s': [25768.91943081631, 26183.826731337853], 'input_queue_cap': [2.0], 'guard_queue_cap_tokens': [515.0, 523.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4638, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 231920.2748773468}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4713, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 235654.44058204067}]
notes: []
