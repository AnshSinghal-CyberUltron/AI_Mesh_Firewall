# li-250: strict FAIL | load-knee FAIL (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 75000 (250.0/s) qualified 73667 (245.56/s) FP-blocks 1333 (0.01777) expected-blocks 0 infra 0 (0.0) drops 0 safety 0 detection-misses 0
by class: {'benign': {'policy_block_fp': 1333, 'qualified': 73667}}
infra reasons: {}
infra detail: {}

T_fw_addon: n=73667 p50=48.1705 p90=55.9207 p99=73.8044 p99.9=83.2667 max=106.6325 mean=39.3374
T_fw_addon_nohold: n=73667 p50=11.884 p90=15.8767 p99=20.4162 p99.9=24.8596 max=64.9477 mean=11.5637
T_fw_addon_sse: n=51590 p50=51.644 p90=57.6975 p99=74.7915 p99.9=88.4813 max=106.6325 mean=51.2181
T_fw_addon_json: n=22077 p50=11.8828 p90=15.9156 p99=20.2996 p99.9=24.7721 max=61.0263 mean=11.5743
T_addon_first_sse: n=51590 p50=11.8015 p90=16.0237 p99=31.0304 p99.9=38.4021 max=64.8421 mean=11.7551
T_addon_total_sse: n=51590 p50=11.8844 p90=15.861 p99=20.499 p99.9=24.8596 max=64.9477 mean=11.5592
T_addon_total_json: n=22077 p50=11.8828 p90=15.9156 p99=20.2996 p99.9=24.7721 max=61.0263 mean=11.5743
T_release_lag_max: n=51590 p50=51.644 p90=57.6975 p99=74.7915 p99.9=88.4813 max=106.6325 mean=51.2181
client_ttft_sse: n=51590 p50=161.8422 p90=166.0663 p99=181.0872 p99.9=188.418 max=214.8739 mean=161.7948
lateness: n=75000 p50=0.0837 p90=0.0931 p99=0.104 p99.9=0.1249 max=0.343 mean=0.0839
loadgen: [{'vm': 'rv-pbu-lg-3', 'busy_max': 12.6, 'busy_mean': 9.3, 'late_max_us': 1007, 'conn_opens': 639, 'max_inflight': 613}, {'vm': 'rv-pbu-lg-4', 'busy_max': 9.6, 'busy_mean': 9.1, 'late_max_us': 341, 'conn_opens': 638, 'max_inflight': 613}]
wire per client request: {'client_to_gw': 7847.0, 'gw_to_client': 56483.0, 'gw_to_provider': 8525.0, 'provider_to_gw': 57165.5}
gateway cores 8.975 cpu-ms/req 36.018 (workers 31.948, owners 4.068, redis 0.164)
worker util {'n': 18, 'min': 0.344, 'median': 0.431, 'max': 0.522, 'all_sorted': [0.344, 0.392, 0.395, 0.397, 0.405, 0.415, 0.418, 0.424, 0.424, 0.431, 0.459, 0.465, 0.467, 0.488, 0.498, 0.503, 0.513, 0.522]}; per-core schedstat max 0.388 mean 0.381; procstat max 0.47
gpu: {'0': {'samples': 298, 'sm_mean': 39.4, 'sm_max': 63.0, 'mem_mean': 7.1, 'fb_mb_max': 434.0}, '1': {'samples': 298, 'sm_mean': 36.4, 'sm_max': 61.0, 'mem_mean': 6.6, 'fb_mb_max': 434.0}}
rss max total by role (MB): {'redis': 2541.7, 'launcher': 59.7, 'owner': 3065.5, 'worker': 3695.0}; fds {'redis': 0, 'launcher': 7, 'owner': 131, 'worker': 2883}
W t_input_ns: {'n': 74956, 'mean_ms': 9.944, 'p50_ms': 10.2892, 'p90_ms': 14.0902, 'p99_ms': 18.7433, 'p99.9_ms': 22.6755, 'max_cum_ms': 53.3673}
W t_admit_ns: {'n': 74953, 'mean_ms': 0.096, 'p50_ms': 0.0906, 'p90_ms': 0.1162, 'p99_ms': 0.1526, 'p99.9_ms': 0.8397, 'max_cum_ms': 3.5161}
W t_tokenize_ns: {'n': 74953, 'mean_ms': 4.0769, 'p50_ms': 3.9813, 'p90_ms': 6.2587, 'p99_ms': 7.3073, 'p99.9_ms': 8.0937, 'max_cum_ms': 45.5791}
W t_det_scan_ns: {'n': 74953, 'mean_ms': 0.1251, 'p50_ms': 0.1203, 'p90_ms': 0.1731, 'p99_ms': 0.2181, 'p99.9_ms': 0.2591, 'max_cum_ms': 1.1228}
W t_guard_wait_ns: {'n': 74956, 'mean_ms': 5.1914, 'p50_ms': 5.1446, 'p90_ms': 7.7005, 'p99_ms': 12.6484, 'p99.9_ms': 16.3185, 'max_cum_ms': 46.2028}
W guard_owner_rtt_ns: {'n': 74956, 'mean_ms': 5.3993, 'p50_ms': 5.4067, 'p90_ms': 7.9626, 'p99_ms': 12.9106, 'p99.9_ms': 16.5806, 'max_cum_ms': 40.4487}
W guard_queue_ns: {'n': 74956, 'mean_ms': 0.9566, 'p50_ms': 0.1275, 'p90_ms': 3.2276, 'p99_ms': 8.0282, 'p99.9_ms': 11.7309, 'max_cum_ms': 19.3312}
W guard_exec_ns: {'n': 74956, 'mean_ms': 3.6329, 'p50_ms': 4.3581, 'p90_ms': 4.6203, 'p99_ms': 6.783, 'p99.9_ms': 6.9796, 'max_cum_ms': 9.4148}
W dispatch_headers_ns: {'n': 73644, 'mean_ms': 1504.115, 'p50_ms': 149.9464, 'p90_ms': 5872.0256, 'p99_ms': 7952.4004, 'p99.9_ms': 8153.727, 'max_cum_ms': 8134.3514}
W release_lag_ns: {'n': 11526452, 'mean_ms': 19.8938, 'p50_ms': 20.054, 'p90_ms': 20.3162, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 100.3546}
W holdback_wait_ns: {'n': 11233916, 'mean_ms': 20.3404, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 100.2734}
W release_processing_ns: {'n': 11526452, 'mean_ms': 0.0697, 'p50_ms': 0.0681, 'p90_ms': 0.0937, 'p99_ms': 0.1254, 'p99.9_ms': 0.1731, 'max_cum_ms': 40.1171}
W t_finalize_ns: {'n': 73657, 'mean_ms': 0.2931, 'p50_ms': 0.2888, 'p90_ms': 0.3707, 'p99_ms': 0.428, 'p99.9_ms': 0.4772, 'max_cum_ms': 4.4128}
W loop_lag_ns: {'n': 53989, 'mean_ms': 0.0752, 'p50_ms': 0.0, 'p90_ms': 0.2099, 'p99_ms': 0.9544, 'p99.9_ms': 1.6138, 'max_cum_ms': 37.9964}
W guard_windows_per_request: {'n': 74953, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_exec_ns: {'n': 74956, 'mean_ms': 3.6329, 'p50_ms': 4.3581, 'p90_ms': 4.6203, 'p99_ms': 6.783, 'p99.9_ms': 6.9796, 'max_cum_ms': 9.4148}
O guard_batch_windows: {'n': 74956, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_queue_ns: {'n': 74956, 'mean_ms': 0.9566, 'p50_ms': 0.1275, 'p90_ms': 3.2276, 'p99_ms': 8.0282, 'p99.9_ms': 11.7309, 'max_cum_ms': 19.3312}
worker counts: {'admitted': 74953, 'audit_enqueued': 148613, 'audit_written': 148612, 'background_round_trips': 10783, 'disposition_ALLOW': 73623, 'disposition_BLOCK': 1333, 'guard_windows': 122978, 'lease_granted_tokens{org="org-a"}': 70465920, 'lease_refills': 343, 'provider_calls': 73623, 'provider_connections_opened': 12690, 'quota_admitted_tokens{org="org-a"}': 70927918, 'requests_by_round_trips{n="0"}': 74610, 'requests_by_round_trips{n="1"}': 343, 'shared_state_round_trips': 343}
owner counts: {'guard_batches': 74956, 'guard_windows': 122978, 'owner_requests': 74953, 'owner_windows': 122973}
gauges: {'audit_queue_bound': [181], 'guard_tokens_per_s': [25859.570924769152, 26277.94409965762], 'audit_completeness_ratio': [0.999981004482942, 1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'input_queue_cap': [68.0, 69.0], 'guard_queue_cap_tokens': [25859.0, 26277.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 232736, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 232736.13832292237}, {'owner_clients': 9, 'owner_queued_tokens': 1024, 'owner_queue_cap_tokens': 236501, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 236501.49689691857}]
notes: []
