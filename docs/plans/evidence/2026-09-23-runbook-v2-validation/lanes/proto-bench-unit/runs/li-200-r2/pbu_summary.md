# li-200-r2: strict FAIL | load-knee PASS (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 60000 (200.0/s) qualified 58936 (196.45/s) FP-blocks 1063 (0.01772) expected-blocks 0 infra 1 (1.6666666666666667e-05) drops 0 safety 0 detection-misses 0
by class: {'benign': {'infra_error': 1, 'policy_block_fp': 1063, 'qualified': 58936}}
infra reasons: {'block_on_unavailable_sem': 1}
infra detail: {'403 http_403 type=policy_violation code=blocked_by_policy': 1}

T_fw_addon: n=58936 p50=47.6892 p90=54.1126 p99=72.5109 p99.9=78.5929 max=113.8458 mean=38.2562
T_fw_addon_nohold: n=58936 p50=10.8167 p90=14.2846 p99=17.837 p99.9=19.8651 max=56.8341 mean=10.5958
T_fw_addon_sse: n=41264 p50=50.4832 p90=55.5471 p99=73.2229 p99.9=87.3347 max=113.8458 mean=50.0662
T_fw_addon_json: n=17672 p50=10.8941 p90=14.4219 p99=17.9626 p99.9=19.8294 max=49.172 mean=10.6799
T_addon_first_sse: n=41264 p50=10.7168 p90=14.477 p99=29.9631 p99.9=35.9498 max=57.2224 mean=10.7827
T_addon_total_sse: n=41264 p50=10.7822 p90=14.2289 p99=17.7881 p99.9=19.8788 max=56.8341 mean=10.5598
T_addon_total_json: n=17672 p50=10.8941 p90=14.4219 p99=17.9626 p99.9=19.8294 max=49.172 mean=10.6799
T_release_lag_max: n=41264 p50=50.4832 p90=55.5471 p99=73.2229 p99.9=87.3347 max=113.8458 mean=50.0662
client_ttft_sse: n=41264 p50=160.7586 p90=164.5297 p99=179.9972 p99.9=185.9569 max=207.2281 mean=160.8236
lateness: n=60000 p50=0.0839 p90=0.0932 p99=0.1038 p99.9=0.1227 max=0.3448 mean=0.084
loadgen: [{'vm': 'rv-pbu-lg-3', 'busy_max': 18.7, 'busy_mean': 7.9, 'late_max_us': 763, 'conn_opens': 520, 'max_inflight': 497}, {'vm': 'rv-pbu-lg-4', 'busy_max': 20.4, 'busy_mean': 7.6, 'late_max_us': 297, 'conn_opens': 521, 'max_inflight': 497}]
wire per client request: {'client_to_gw': 7860.8, 'gw_to_client': 56391.5, 'gw_to_provider': 8565.0, 'provider_to_gw': 57072.2}
gateway cores 7.088 cpu-ms/req 35.559 (workers 31.509, owners 4.047, redis 0.167)
worker util {'n': 18, 'min': 0.298, 'median': 0.343, 'max': 0.413, 'all_sorted': [0.298, 0.311, 0.316, 0.321, 0.325, 0.325, 0.333, 0.334, 0.335, 0.343, 0.348, 0.349, 0.373, 0.376, 0.385, 0.395, 0.4, 0.413]}; per-core schedstat max 0.314 mean 0.302; procstat max 0.423
gpu: {'0': {'samples': 298, 'sm_mean': 30.7, 'sm_max': 56.0, 'mem_mean': 5.6, 'fb_mb_max': 434.0}, '1': {'samples': 298, 'sm_mean': 29.4, 'sm_max': 54.0, 'mem_mean': 5.3, 'fb_mb_max': 434.0}}
rss max total by role (MB): {'redis': 1625.0, 'launcher': 59.7, 'owner': 3065.5, 'worker': 3685.4}; fds {'redis': 0, 'launcher': 7, 'owner': 126, 'worker': 2421}
W t_input_ns: {'n': 59965, 'mean_ms': 9.0638, 'p50_ms': 9.5027, 'p90_ms': 12.6484, 'p99_ms': 16.0563, 'p99.9_ms': 18.219, 'max_cum_ms': 48.3649}
W t_admit_ns: {'n': 59963, 'mean_ms': 0.0946, 'p50_ms': 0.0896, 'p90_ms': 0.1132, 'p99_ms': 0.1485, 'p99.9_ms': 0.7332, 'max_cum_ms': 3.1884}
W t_tokenize_ns: {'n': 59964, 'mean_ms': 3.8273, 'p50_ms': 3.7847, 'p90_ms': 5.7999, 'p99_ms': 6.914, 'p99.9_ms': 7.766, 'max_cum_ms': 44.3103}
W t_det_scan_ns: {'n': 59964, 'mean_ms': 0.1192, 'p50_ms': 0.1121, 'p90_ms': 0.169, 'p99_ms': 0.2181, 'p99.9_ms': 0.2529, 'max_cum_ms': 1.1228}
W t_guard_wait_ns: {'n': 59965, 'mean_ms': 4.5964, 'p50_ms': 4.948, 'p90_ms': 7.0451, 'p99_ms': 9.8959, 'p99.9_ms': 12.1242, 'max_cum_ms': 43.2734}
W guard_owner_rtt_ns: {'n': 59965, 'mean_ms': 4.8005, 'p50_ms': 5.1446, 'p90_ms': 7.3073, 'p99_ms': 10.2892, 'p99.9_ms': 12.2552, 'max_cum_ms': 40.4487}
W guard_queue_ns: {'n': 59964, 'mean_ms': 0.4426, 'p50_ms': 0.1152, 'p90_ms': 1.3025, 'p99_ms': 5.2101, 'p99.9_ms': 7.766, 'max_cum_ms': 16.4869}
W guard_exec_ns: {'n': 59964, 'mean_ms': 3.6074, 'p50_ms': 4.2926, 'p90_ms': 4.5548, 'p99_ms': 6.7174, 'p99.9_ms': 6.8485, 'max_cum_ms': 9.4148}
W dispatch_headers_ns: {'n': 58955, 'mean_ms': 1506.2949, 'p50_ms': 149.9464, 'p90_ms': 5872.0256, 'p99_ms': 7952.4004, 'p99.9_ms': 8153.727, 'max_cum_ms': 8134.3514}
W release_lag_ns: {'n': 9202268, 'mean_ms': 19.8918, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 100.1277}
W holdback_wait_ns: {'n': 8968637, 'mean_ms': 20.3396, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 100.0541}
W release_processing_ns: {'n': 9202268, 'mean_ms': 0.0686, 'p50_ms': 0.0671, 'p90_ms': 0.0916, 'p99_ms': 0.1224, 'p99.9_ms': 0.1649, 'max_cum_ms': 35.4319}
W t_finalize_ns: {'n': 58949, 'mean_ms': 0.2847, 'p50_ms': 0.2806, 'p90_ms': 0.3584, 'p99_ms': 0.4157, 'p99.9_ms': 0.469, 'max_cum_ms': 3.3847}
W loop_lag_ns: {'n': 53986, 'mean_ms': 0.0717, 'p50_ms': 0.0, 'p90_ms': 0.1915, 'p99_ms': 0.9871, 'p99.9_ms': 1.5811, 'max_cum_ms': 37.9964}
W guard_windows_per_request: {'n': 59964, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_exec_ns: {'n': 59964, 'mean_ms': 3.6074, 'p50_ms': 4.2926, 'p90_ms': 4.5548, 'p99_ms': 6.7174, 'p99.9_ms': 6.8485, 'max_cum_ms': 9.4148}
O guard_batch_windows: {'n': 59964, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_queue_ns: {'n': 59964, 'mean_ms': 0.4426, 'p50_ms': 0.1152, 'p90_ms': 1.3025, 'p99_ms': 5.2101, 'p99.9_ms': 7.766, 'max_cum_ms': 16.4869}
worker counts: {'admitted': 59963, 'audit_enqueued': 118914, 'audit_written': 118913, 'background_round_trips': 10785, 'disposition_ALLOW': 58901, 'disposition_BLOCK': 1064, 'guard_deadline_expired': 1, 'guard_unavailable_findings': 1, 'guard_windows': 98456, 'lease_granted_tokens{org="org-a"}': 56906880, 'lease_refills': 277, 'provider_calls': 58901, 'provider_connections_opened': 10193, 'quota_admitted_tokens{org="org-a"}': 56728846, 'requests_by_round_trips{n="0"}': 59686, 'requests_by_round_trips{n="1"}': 277, 'shared_state_round_trips': 277}
owner counts: {'guard_batches': 59964, 'guard_deadline_expired': 1, 'guard_windows': 98456, 'owner_requests': 59964, 'owner_windows': 98456}
gauges: {'audit_queue_bound': [181], 'guard_tokens_per_s': [25859.570924769152, 26277.94409965762], 'audit_completeness_ratio': [0.9999739087327472, 1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'input_queue_cap': [68.0, 69.0], 'guard_queue_cap_tokens': [25859.0, 26277.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 232736, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 232736.13832292237}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 236501, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 236501.49689691857}]
notes: []
