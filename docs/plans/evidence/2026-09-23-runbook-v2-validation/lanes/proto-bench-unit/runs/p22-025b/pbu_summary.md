# p22-025b: strict FAIL | load-knee FAIL (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': False, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': False, 'zero_safety_failures': True, 'run_valid': True}
offered 7500 (25.0/s) qualified 7398 (24.66/s) FP-blocks 102 (0.0136) expected-blocks 0 infra 0 (0.0) drops 1 safety 0 detection-misses 0
by class: {'benign': {'qualified': 7398, 'policy_block_fp': 102}}
infra reasons: {}
infra detail: {}

T_fw_addon: n=7398 p50=9.8013 p90=14.1683 p99=52.9633 p99.9=70.4337 max=73.1928 mean=11.9484
T_fw_addon_nohold: n=7398 p50=9.556 p90=12.1468 p99=14.5423 p99.9=18.9991 max=22.4418 mean=8.9261
T_fw_addon_sse: n=5172 p50=9.8673 p90=30.6868 p99=54.004 p99.9=70.634 max=73.1928 mean=13.2351
T_fw_addon_json: n=2226 p50=9.6306 p90=12.3758 p99=14.7834 p99.9=18.8687 max=19.3023 mean=8.9589
T_addon_first_sse: n=5172 p50=9.4463 p90=12.7962 p99=26.6816 p99.9=33.1881 max=50.3938 mean=9.1275
T_addon_total_sse: n=5172 p50=9.5291 p90=12.0589 p99=14.4013 p99.9=19.0841 max=22.4418 mean=8.9121
T_addon_total_json: n=2226 p50=9.6306 p90=12.3758 p99=14.7834 p99.9=18.8687 max=19.3023 mean=8.9589
T_release_lag_max: n=524 p50=49.2802 p90=53.9842 p99=70.634 p99.9=73.1928 max=73.1928 mean=48.4039
client_ttft_sse: n=5172 p50=159.4842 p90=162.8482 p99=176.7112 p99.9=183.2152 max=200.3996 mean=159.1766
lateness: n=7500 p50=0.0917 p90=0.107 p99=0.1234 p99.9=0.1405 max=9.3147 mean=0.0943
loadgen: [{'vm': 'rv-pbu-lg-1', 'busy_max': 17.7, 'busy_mean': 4.1, 'late_max_us': 9314, 'conn_opens': 74, 'max_inflight': 70}, {'vm': 'rv-pbu-lg-2', 'busy_max': 15.6, 'busy_mean': 4.2, 'late_max_us': 282, 'conn_opens': 74, 'max_inflight': 70}]
wire per client request: {'client_to_gw': 9207.5, 'gw_to_client': 56865.8, 'gw_to_provider': 9859.5, 'provider_to_gw': 57521.3}
gateway cores 0.99 cpu-ms/req 39.719 (workers 35.485, owners 4.217, redis 0.287)
worker util {'n': 18, 'min': 0.015, 'median': 0.046, 'max': 0.089}; per-core schedstat max 0.059 mean 0.044; procstat max 0.085
gpu: {'0': {'samples': 298, 'sm_mean': 3.5, 'sm_max': 14.0, 'mem_mean': 0.6, 'fb_mb_max': 434.0}, '1': {'samples': 298, 'sm_mean': 4.4, 'sm_max': 15.0, 'mem_mean': 0.8, 'fb_mb_max': 434.0}}
rss max total by role (MB): {'redis': 1709.8, 'launcher': 59.7, 'owner': 3067.1, 'worker': 3674.7}; fds {'redis': 0, 'launcher': 7, 'owner': 129, 'worker': 729}
W t_input_ns: {'n': 7498, 'mean_ms': 7.6278, 'p50_ms': 8.3558, 'p90_ms': 10.1581, 'p99_ms': 12.5174, 'p99.9_ms': 16.5806, 'max_cum_ms': 31.6019}
W t_admit_ns: {'n': 7498, 'mean_ms': 0.0934, 'p50_ms': 0.0886, 'p90_ms': 0.1091, 'p99_ms': 0.1444, 'p99.9_ms': 0.6431, 'max_cum_ms': 3.9185}
W t_tokenize_ns: {'n': 7498, 'mean_ms': 2.7903, 'p50_ms': 2.8344, 'p90_ms': 4.2271, 'p99_ms': 4.7514, 'p99.9_ms': 5.7999, 'max_cum_ms': 8.0838}
W t_det_scan_ns: {'n': 7498, 'mean_ms': 0.0996, 'p50_ms': 0.1009, 'p90_ms': 0.1224, 'p99_ms': 0.1505, 'p99.9_ms': 0.1812, 'max_cum_ms': 24.0812}
W t_guard_wait_ns: {'n': 7498, 'mean_ms': 4.2891, 'p50_ms': 4.8824, 'p90_ms': 5.2101, 'p99_ms': 7.3073, 'p99.9_ms': 12.2552, 'max_cum_ms': 29.4642}
W guard_owner_rtt_ns: {'n': 7498, 'mean_ms': 4.4596, 'p50_ms': 5.079, 'p90_ms': 5.4723, 'p99_ms': 7.5039, 'p99.9_ms': 11.5999, 'max_cum_ms': 28.5289}
W guard_queue_ns: {'n': 7498, 'mean_ms': 0.1161, 'p50_ms': 0.1142, 'p90_ms': 0.1321, 'p99_ms': 0.1546, 'p99.9_ms': 0.9626, 'max_cum_ms': 12.2232}
W guard_exec_ns: {'n': 7498, 'mean_ms': 3.6441, 'p50_ms': 4.2926, 'p90_ms': 4.4892, 'p99_ms': 6.5208, 'p99.9_ms': 6.7174, 'max_cum_ms': 7.9147}
W dispatch_headers_ns: {'n': 7407, 'mean_ms': 1542.2274, 'p50_ms': 149.9464, 'p90_ms': 5939.1345, 'p99_ms': 7885.2915, 'p99.9_ms': 8153.727, 'max_cum_ms': 8139.2743}
W release_lag_ns: {'n': 1156180, 'mean_ms': 19.8849, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 100.1673}
W holdback_wait_ns: {'n': 1126649, 'mean_ms': 20.34, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 100.0223}
W release_processing_ns: {'n': 1156180, 'mean_ms': 0.0645, 'p50_ms': 0.0637, 'p90_ms': 0.0845, 'p99_ms': 0.1101, 'p99.9_ms': 0.1423, 'max_cum_ms': 26.6219}
W t_finalize_ns: {'n': 7409, 'mean_ms': 0.2555, 'p50_ms': 0.2509, 'p90_ms': 0.3092, 'p99_ms': 0.3748, 'p99.9_ms': 0.4157, 'max_cum_ms': 2.7484}
W loop_lag_ns: {'n': 53630, 'mean_ms': 0.7569, 'p50_ms': 0.0314, 'p90_ms': 1.3517, 'p99_ms': 7.4383, 'p99.9_ms': 8.5852, 'max_cum_ms': 56.9536}
W guard_windows_per_request: {'n': 7498, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_exec_ns: {'n': 7497, 'mean_ms': 3.6438, 'p50_ms': 4.2926, 'p90_ms': 4.4892, 'p99_ms': 6.5208, 'p99.9_ms': 6.7174, 'max_cum_ms': 7.9147}
O guard_batch_windows: {'n': 7497, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_queue_ns: {'n': 7497, 'mean_ms': 0.1161, 'p50_ms': 0.1142, 'p90_ms': 0.1321, 'p99_ms': 0.1546, 'p99.9_ms': 0.9626, 'max_cum_ms': 12.2232}
worker counts: {'admitted': 7498, 'audit_enqueued': 14907, 'audit_written': 14907, 'background_round_trips': 10726, 'disposition_ALLOW': 7396, 'disposition_BLOCK': 102, 'guard_windows': 12368, 'lease_granted_tokens{org="org-a"}': 6984960, 'lease_refills': 34, 'provider_calls': 7396, 'provider_connections_opened': 1130, 'quota_admitted_tokens{org="org-a"}': 7109767, 'requests_by_round_trips{n="0"}': 7464, 'requests_by_round_trips{n="1"}': 34, 'shared_state_round_trips': 34}
owner counts: {'guard_batches': 7497, 'guard_windows': 12365, 'owner_requests': 7497, 'owner_windows': 12365}
gauges: {'audit_queue_bound': [181], 'audit_completeness_ratio': [1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'guard_tokens_per_s': [25768.91943081631, 26183.826731337853], 'input_queue_cap': [2.0], 'guard_queue_cap_tokens': [515.0, 523.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4638, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 231920.2748773468}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4713, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 235654.44058204067}]
notes: []
