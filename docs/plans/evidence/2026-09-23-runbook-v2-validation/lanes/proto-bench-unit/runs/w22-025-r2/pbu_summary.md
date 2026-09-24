# w22-025-r2: strict FAIL | load-knee PASS (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 7500 (25.0/s) qualified 7333 (24.44/s) FP-blocks 167 (0.02227) expected-blocks 0 infra 0 (0.0) drops 0 safety 0 detection-misses 0
by class: {'benign': {'policy_block_fp': 167, 'qualified': 7333}}
infra reasons: {}
infra detail: {}

T_fw_addon: n=7333 p50=14.462 p90=17.0854 p99=57.6614 p99.9=74.5387 max=94.6437 mean=17.5552
T_fw_addon_nohold: n=7333 p50=14.3231 p90=14.9067 p99=19.2034 p99.9=22.0784 max=24.5168 mean=14.3749
T_fw_addon_sse: n=5130 p50=14.4652 p90=34.0175 p99=73.0644 p99.9=74.6291 max=94.6437 mean=18.8653
T_fw_addon_json: n=2203 p50=14.4559 p90=14.9952 p99=19.1932 p99.9=22.2868 max=24.5168 mean=14.5046
T_addon_first_sse: n=5130 p50=14.1996 p90=15.9755 p99=33.6328 p99.9=34.7526 max=53.8032 mean=14.6669
T_addon_total_sse: n=5130 p50=14.2709 p90=14.8476 p99=19.2723 p99.9=21.9777 max=22.8886 mean=14.3191
T_addon_total_json: n=2203 p50=14.4559 p90=14.9952 p99=19.1932 p99.9=22.2868 max=24.5168 mean=14.5046
T_release_lag_max: n=485 p50=54.4477 p90=73.2491 p99=74.6366 p99.9=94.6437 max=94.6437 mean=57.0109
client_ttft_sse: n=5130 p50=164.2456 p90=166.0236 p99=183.6878 p99.9=184.7793 max=203.8129 mean=164.7128
lateness: n=7500 p50=0.0861 p90=0.1018 p99=0.1339 p99.9=0.2131 max=0.3724 mean=0.0851
loadgen: [{'vm': 'rv-pbu-lg-1', 'busy_max': 8.2, 'busy_mean': 4.8, 'late_max_us': 372, 'conn_opens': 103, 'max_inflight': 102}, {'vm': 'rv-pbu-lg-2', 'busy_max': 5.6, 'busy_mean': 4.7, 'late_max_us': 240, 'conn_opens': 103, 'max_inflight': 102}]
wire per client request: {'client_to_gw': 13839.7, 'gw_to_client': 98321.1, 'gw_to_provider': 13433.3, 'provider_to_gw': 99664.0}
gateway cores 1.464 cpu-ms/req 58.775 (workers 51.698, owners 7.06, redis 0.288)
worker util {'n': 18, 'min': 0.052, 'median': 0.069, 'max': 0.1}; per-core schedstat max 0.116 mean 0.065; procstat max 0.115
gpu: {'0': {'samples': 298, 'sm_mean': 7.1, 'sm_max': 17.0, 'mem_mean': 1.3, 'fb_mb_max': 434.0}, '1': {'samples': 298, 'sm_mean': 6.7, 'sm_max': 16.0, 'mem_mean': 1.2, 'fb_mb_max': 434.0}}
rss max total by role (MB): {'redis': 2776.3, 'launcher': 59.7, 'owner': 3067.2, 'worker': 3697.1}; fds {'redis': 0, 'launcher': 7, 'owner': 131, 'worker': 858}
W t_input_ns: {'n': 7492, 'mean_ms': 12.8108, 'p50_ms': 12.7795, 'p90_ms': 13.1727, 'p99_ms': 15.1388, 'p99.9_ms': 20.054, 'max_cum_ms': 32.2244}
W t_admit_ns: {'n': 7492, 'mean_ms': 0.0942, 'p50_ms': 0.0896, 'p90_ms': 0.0988, 'p99_ms': 0.1976, 'p99.9_ms': 0.51, 'max_cum_ms': 3.9185}
W t_tokenize_ns: {'n': 7492, 'mean_ms': 5.1193, 'p50_ms': 5.2101, 'p90_ms': 5.4723, 'p99_ms': 5.8655, 'p99.9_ms': 7.7005, 'max_cum_ms': 8.7407}
W t_det_scan_ns: {'n': 7492, 'mean_ms': 0.1245, 'p50_ms': 0.1213, 'p90_ms': 0.1362, 'p99_ms': 0.1772, 'p99.9_ms': 0.212, 'max_cum_ms': 24.0812}
W t_guard_wait_ns: {'n': 7492, 'mean_ms': 7.0984, 'p50_ms': 7.0451, 'p90_ms': 7.1762, 'p99_ms': 8.0282, 'p99.9_ms': 13.9592, 'max_cum_ms': 29.4642}
W guard_owner_rtt_ns: {'n': 7492, 'mean_ms': 7.3335, 'p50_ms': 7.3073, 'p90_ms': 7.4383, 'p99_ms': 8.1592, 'p99.9_ms': 13.697, 'max_cum_ms': 28.5289}
W guard_queue_ns: {'n': 7492, 'mean_ms': 0.1233, 'p50_ms': 0.1213, 'p90_ms': 0.1382, 'p99_ms': 0.1628, 'p99.9_ms': 0.1956, 'max_cum_ms': 12.2232}
W guard_exec_ns: {'n': 7492, 'mean_ms': 6.4601, 'p50_ms': 6.4553, 'p90_ms': 6.5864, 'p99_ms': 6.7174, 'p99.9_ms': 7.2417, 'max_cum_ms': 8.4399}
W dispatch_headers_ns: {'n': 7331, 'mean_ms': 2551.227, 'p50_ms': 149.9464, 'p90_ms': 8153.727, 'p99_ms': 8153.727, 'p99.9_ms': 8153.727, 'max_cum_ms': 8151.0442}
W release_lag_ns: {'n': 2022171, 'mean_ms': 19.9651, 'p50_ms': 20.054, 'p90_ms': 20.3162, 'p99_ms': 40.108, 'p99.9_ms': 40.6323, 'max_cum_ms': 150.0061}
W holdback_wait_ns: {'n': 1978566, 'mean_ms': 20.3419, 'p50_ms': 20.054, 'p90_ms': 20.3162, 'p99_ms': 40.108, 'p99.9_ms': 40.6323, 'max_cum_ms': 149.9429}
W release_processing_ns: {'n': 2022171, 'mean_ms': 0.0619, 'p50_ms': 0.0596, 'p90_ms': 0.0896, 'p99_ms': 0.1244, 'p99.9_ms': 0.1587, 'max_cum_ms': 26.6219}
W t_finalize_ns: {'n': 7333, 'mean_ms': 0.3027, 'p50_ms': 0.297, 'p90_ms': 0.383, 'p99_ms': 0.4485, 'p99.9_ms': 0.4895, 'max_cum_ms': 2.7484}
W loop_lag_ns: {'n': 53590, 'mean_ms': 0.7852, 'p50_ms': 0.0957, 'p90_ms': 1.2698, 'p99_ms': 7.6349, 'p99.9_ms': 10.1581, 'max_cum_ms': 56.9536}
W guard_windows_per_request: {'n': 7492, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_exec_ns: {'n': 7504, 'mean_ms': 6.4601, 'p50_ms': 6.4553, 'p90_ms': 6.5864, 'p99_ms': 6.7174, 'p99.9_ms': 7.2417, 'max_cum_ms': 8.4399}
O guard_batch_windows: {'n': 7504, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_queue_ns: {'n': 7504, 'mean_ms': 0.1233, 'p50_ms': 0.1213, 'p90_ms': 0.1382, 'p99_ms': 0.1628, 'p99.9_ms': 0.1956, 'max_cum_ms': 12.2232}
worker counts: {'admitted': 7492, 'audit_enqueued': 14825, 'audit_written': 14825, 'background_round_trips': 10718, 'disposition_ALLOW': 7325, 'disposition_BLOCK': 167, 'guard_windows': 22476, 'lease_granted_tokens{org="org-a"}': 12326400, 'lease_refills': 60, 'provider_calls': 7325, 'provider_connections_opened': 711, 'quota_admitted_tokens{org="org-a"}': 12577647, 'requests_by_round_trips{n="0"}': 7432, 'requests_by_round_trips{n="1"}': 60, 'shared_state_round_trips': 60}
owner counts: {'guard_batches': 7504, 'guard_windows': 22512, 'owner_requests': 7504, 'owner_windows': 22512}
gauges: {'audit_queue_bound': [181], 'audit_completeness_ratio': [1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'guard_tokens_per_s': [25768.91943081631, 26183.826731337853], 'input_queue_cap': [2.0], 'guard_queue_cap_tokens': [515.0, 523.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4638, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 231920.2748773468}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4713, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 235654.44058204067}]
notes: []
