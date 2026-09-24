# w22-025: strict FAIL | load-knee PASS (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 7500 (25.0/s) qualified 7338 (24.46/s) FP-blocks 162 (0.0216) expected-blocks 0 infra 0 (0.0) drops 0 safety 0 detection-misses 0
by class: {'benign': {'policy_block_fp': 162, 'qualified': 7338}}
infra reasons: {}
infra detail: {}

T_fw_addon: n=7338 p50=14.3879 p90=17.0288 p99=57.0208 p99.9=74.3068 max=113.9174 mean=17.5577
T_fw_addon_nohold: n=7338 p50=14.259 p90=14.8752 p99=18.9713 p99.9=21.9683 max=37.6966 mean=14.3237
T_fw_addon_sse: n=5134 p50=14.3875 p90=34.4709 p99=72.9114 p99.9=74.5988 max=113.9174 mean=18.889
T_fw_addon_json: n=2204 p50=14.3895 p90=14.9809 p99=18.7219 p99.9=22.0752 max=26.8731 mean=14.4566
T_addon_first_sse: n=5134 p50=14.1101 p90=14.9585 p99=33.8935 p99.9=34.8841 max=54.3435 mean=14.6171
T_addon_total_sse: n=5134 p50=14.2028 p90=14.8149 p99=19.0875 p99.9=21.9083 max=37.6966 mean=14.2666
T_addon_total_json: n=2204 p50=14.3895 p90=14.9809 p99=18.7219 p99.9=22.0752 max=26.8731 mean=14.4566
T_release_lag_max: n=497 p50=54.3348 p90=72.974 p99=74.8023 p99.9=113.9174 max=113.9174 mean=56.7262
client_ttft_sse: n=5134 p50=164.158 p90=165.0078 p99=183.9119 p99.9=184.8866 max=204.3673 mean=164.6648
lateness: n=7500 p50=0.0833 p90=0.0953 p99=0.108 p99.9=0.1261 max=0.1848 mean=0.0774
loadgen: [{'vm': 'rv-pbu-lg-1', 'busy_max': 15.2, 'busy_mean': 4.9, 'late_max_us': 224, 'conn_opens': 103, 'max_inflight': 102}, {'vm': 'rv-pbu-lg-2', 'busy_max': 16.7, 'busy_mean': 4.6, 'late_max_us': 332, 'conn_opens': 103, 'max_inflight': 102}]
wire per client request: {'client_to_gw': 13819.0, 'gw_to_client': 98368.5, 'gw_to_provider': 13490.2, 'provider_to_gw': 99725.3}
gateway cores 1.451 cpu-ms/req 58.225 (workers 51.173, owners 7.035, redis 0.289)
worker util {'n': 18, 'min': 0.052, 'median': 0.069, 'max': 0.105}; per-core schedstat max 0.09 mean 0.063; procstat max 0.09
gpu: {'0': {'samples': 298, 'sm_mean': 6.6, 'sm_max': 17.0, 'mem_mean': 1.2, 'fb_mb_max': 434.0}, '1': {'samples': 298, 'sm_mean': 7.2, 'sm_max': 16.0, 'mem_mean': 1.3, 'fb_mb_max': 434.0}}
rss max total by role (MB): {'redis': 2492.7, 'launcher': 59.7, 'owner': 3067.2, 'worker': 3689.8}; fds {'redis': 0, 'launcher': 7, 'owner': 129, 'worker': 858}
W t_input_ns: {'n': 7493, 'mean_ms': 12.7679, 'p50_ms': 12.7795, 'p90_ms': 13.1727, 'p99_ms': 14.8767, 'p99.9_ms': 19.7919, 'max_cum_ms': 32.2244}
W t_admit_ns: {'n': 7493, 'mean_ms': 0.0931, 'p50_ms': 0.0886, 'p90_ms': 0.0947, 'p99_ms': 0.2079, 'p99.9_ms': 0.5059, 'max_cum_ms': 3.9185}
W t_tokenize_ns: {'n': 7493, 'mean_ms': 5.1085, 'p50_ms': 5.1446, 'p90_ms': 5.4723, 'p99_ms': 5.931, 'p99.9_ms': 7.6349, 'max_cum_ms': 8.3237}
W t_det_scan_ns: {'n': 7493, 'mean_ms': 0.1247, 'p50_ms': 0.1203, 'p90_ms': 0.1382, 'p99_ms': 0.1833, 'p99.9_ms': 0.2079, 'max_cum_ms': 24.0812}
W t_guard_wait_ns: {'n': 7493, 'mean_ms': 7.0708, 'p50_ms': 7.0451, 'p90_ms': 7.1762, 'p99_ms': 7.8971, 'p99.9_ms': 13.8281, 'max_cum_ms': 29.4642}
W guard_owner_rtt_ns: {'n': 7493, 'mean_ms': 7.3065, 'p50_ms': 7.3073, 'p90_ms': 7.4383, 'p99_ms': 8.0282, 'p99.9_ms': 13.3038, 'max_cum_ms': 28.5289}
W guard_queue_ns: {'n': 7493, 'mean_ms': 0.1219, 'p50_ms': 0.1193, 'p90_ms': 0.1382, 'p99_ms': 0.1628, 'p99.9_ms': 0.1894, 'max_cum_ms': 12.2232}
W guard_exec_ns: {'n': 7493, 'mean_ms': 6.4377, 'p50_ms': 6.4553, 'p90_ms': 6.5208, 'p99_ms': 6.7174, 'p99.9_ms': 7.1762, 'max_cum_ms': 8.4399}
W dispatch_headers_ns: {'n': 7331, 'mean_ms': 2551.1896, 'p50_ms': 149.9464, 'p90_ms': 8153.727, 'p99_ms': 8153.727, 'p99.9_ms': 8153.727, 'max_cum_ms': 8151.0442}
W release_lag_ns: {'n': 2022579, 'mean_ms': 19.9614, 'p50_ms': 20.054, 'p90_ms': 20.3162, 'p99_ms': 40.108, 'p99.9_ms': 40.6323, 'max_cum_ms': 150.0061}
W holdback_wait_ns: {'n': 1978625, 'mean_ms': 20.3423, 'p50_ms': 20.054, 'p90_ms': 20.3162, 'p99_ms': 40.108, 'p99.9_ms': 40.6323, 'max_cum_ms': 149.9429}
W release_processing_ns: {'n': 2022579, 'mean_ms': 0.0612, 'p50_ms': 0.0586, 'p90_ms': 0.0886, 'p99_ms': 0.1224, 'p99.9_ms': 0.1567, 'max_cum_ms': 26.6219}
W t_finalize_ns: {'n': 7338, 'mean_ms': 0.2986, 'p50_ms': 0.2929, 'p90_ms': 0.3789, 'p99_ms': 0.4403, 'p99.9_ms': 0.4895, 'max_cum_ms': 2.7484}
W loop_lag_ns: {'n': 53580, 'mean_ms': 0.7792, 'p50_ms': 0.0886, 'p90_ms': 1.2534, 'p99_ms': 7.5694, 'p99.9_ms': 9.2406, 'max_cum_ms': 56.9536}
W guard_windows_per_request: {'n': 7493, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_exec_ns: {'n': 7499, 'mean_ms': 6.4377, 'p50_ms': 6.4553, 'p90_ms': 6.5208, 'p99_ms': 6.7174, 'p99.9_ms': 7.1762, 'max_cum_ms': 8.4399}
O guard_batch_windows: {'n': 7499, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_queue_ns: {'n': 7499, 'mean_ms': 0.1219, 'p50_ms': 0.1193, 'p90_ms': 0.1382, 'p99_ms': 0.1628, 'p99.9_ms': 0.1956, 'max_cum_ms': 12.2232}
worker counts: {'admitted': 7493, 'audit_enqueued': 14831, 'audit_written': 14830, 'background_round_trips': 10716, 'disposition_ALLOW': 7331, 'disposition_BLOCK': 162, 'guard_windows': 22479, 'lease_granted_tokens{org="org-a"}': 12531840, 'lease_refills': 61, 'provider_calls': 7331, 'provider_connections_opened': 759, 'quota_admitted_tokens{org="org-a"}': 12580309, 'requests_by_round_trips{n="0"}': 7432, 'requests_by_round_trips{n="1"}': 61, 'shared_state_round_trips': 61}
owner counts: {'guard_batches': 7499, 'guard_windows': 22497, 'owner_requests': 7498, 'owner_windows': 22494}
gauges: {'audit_queue_bound': [181], 'audit_completeness_ratio': [0.9999816715542522, 1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'guard_tokens_per_s': [25768.91943081631, 26183.826731337853], 'input_queue_cap': [2.0], 'guard_queue_cap_tokens': [515.0, 523.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4638, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 231920.2748773468}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4713, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 235654.44058204067}]
notes: []
