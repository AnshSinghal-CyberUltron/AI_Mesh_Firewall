# li-100: strict FAIL | load-knee PASS (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 30000 (100.0/s) qualified 29470 (98.23/s) FP-blocks 530 (0.01767) expected-blocks 0 infra 0 (0.0) drops 0 safety 0 detection-misses 0
by class: {'benign': {'policy_block_fp': 530, 'qualified': 29470}}
infra reasons: {}
infra detail: {}

T_fw_addon: n=29470 p50=46.6631 p90=52.2626 p99=71.1746 p99.9=74.754 max=106.5382 mean=36.9834
T_fw_addon_nohold: n=29470 p50=9.8575 p90=12.4238 p99=14.7722 p99.9=15.9724 max=22.4729 mean=9.3418
T_fw_addon_sse: n=20650 p50=49.5293 p90=53.5218 p99=71.5925 p99.9=86.9705 max=106.5382 mean=48.7639
T_fw_addon_json: n=8820 p50=9.8925 p90=12.5358 p99=14.8375 p99.9=16.1137 max=18.4893 mean=9.4021
T_addon_first_sse: n=20650 p50=9.7933 p90=12.6894 p99=29.2485 p99.9=33.5059 max=53.5185 mean=9.5706
T_addon_total_sse: n=20650 p50=9.8444 p90=12.3689 p99=14.7422 p99.9=15.8958 max=22.4729 mean=9.316
T_addon_total_json: n=8820 p50=9.8925 p90=12.5358 p99=14.8375 p99.9=16.1137 max=18.4893 mean=9.4021
T_release_lag_max: n=20650 p50=49.5293 p90=53.5218 p99=71.5925 p99.9=86.9705 max=106.5382 mean=48.7639
client_ttft_sse: n=20650 p50=159.8359 p90=162.7328 p99=179.3069 p99.9=183.5321 max=203.5894 mean=159.6148
lateness: n=30000 p50=0.0878 p90=0.1003 p99=0.1177 p99.9=0.14 max=0.3079 mean=0.0888
loadgen: [{'vm': 'rv-pbu-lg-3', 'busy_max': 8.7, 'busy_mean': 5.4, 'late_max_us': 514, 'conn_opens': 278, 'max_inflight': 259}, {'vm': 'rv-pbu-lg-4', 'busy_max': 5.8, 'busy_mean': 5.3, 'late_max_us': 277, 'conn_opens': 278, 'max_inflight': 259}]
wire per client request: {'client_to_gw': 8017.3, 'gw_to_client': 56627.7, 'gw_to_provider': 8927.3, 'provider_to_gw': 57170.1}
gateway cores 3.418 cpu-ms/req 34.29 (workers 30.257, owners 4.029, redis 0.186)
worker util {'n': 18, 'min': 0.102, 'median': 0.169, 'max': 0.215, 'all_sorted': [0.102, 0.117, 0.131, 0.146, 0.15, 0.154, 0.158, 0.159, 0.165, 0.169, 0.178, 0.185, 0.188, 0.193, 0.2, 0.201, 0.205, 0.215]}; per-core schedstat max 0.162 mean 0.148; procstat max 0.28
gpu: {'0': {'samples': 298, 'sm_mean': 14.0, 'sm_max': 38.0, 'mem_mean': 2.5, 'fb_mb_max': 434.0}, '1': {'samples': 298, 'sm_mean': 16.6, 'sm_max': 34.0, 'mem_mean': 3.0, 'fb_mb_max': 434.0}}
rss max total by role (MB): {'redis': 402.7, 'launcher': 59.7, 'owner': 3065.3, 'worker': 3647.5}; fds {'redis': 0, 'launcher': 7, 'owner': 126, 'worker': 1465}
W t_input_ns: {'n': 29982, 'mean_ms': 7.9636, 'p50_ms': 8.5852, 'p90_ms': 10.6824, 'p99_ms': 13.4349, 'p99.9_ms': 14.0902, 'max_cum_ms': 21.2111}
W t_admit_ns: {'n': 29982, 'mean_ms': 0.0929, 'p50_ms': 0.0876, 'p90_ms': 0.1101, 'p99_ms': 0.1423, 'p99.9_ms': 0.7168, 'max_cum_ms': 3.0789}
W t_tokenize_ns: {'n': 29982, 'mean_ms': 3.2258, 'p50_ms': 3.2604, 'p90_ms': 4.7514, 'p99_ms': 5.6033, 'p99.9_ms': 6.5864, 'max_cum_ms': 12.314}
W t_det_scan_ns: {'n': 29982, 'mean_ms': 0.1034, 'p50_ms': 0.0998, 'p90_ms': 0.1362, 'p99_ms': 0.1894, 'p99.9_ms': 0.2304, 'max_cum_ms': 1.1228}
W t_guard_wait_ns: {'n': 29982, 'mean_ms': 4.1521, 'p50_ms': 4.7514, 'p90_ms': 5.2101, 'p99_ms': 7.2417, 'p99.9_ms': 7.4383, 'max_cum_ms': 10.4826}
W guard_owner_rtt_ns: {'n': 29982, 'mean_ms': 4.3366, 'p50_ms': 5.0135, 'p90_ms': 5.4067, 'p99_ms': 7.5694, 'p99.9_ms': 7.766, 'max_cum_ms': 11.4114}
W guard_queue_ns: {'n': 29982, 'mean_ms': 0.1064, 'p50_ms': 0.1029, 'p90_ms': 0.1295, 'p99_ms': 0.1669, 'p99.9_ms': 0.5018, 'max_cum_ms': 4.4954}
W guard_exec_ns: {'n': 29982, 'mean_ms': 3.5839, 'p50_ms': 4.2271, 'p90_ms': 4.4892, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 9.4148}
W dispatch_headers_ns: {'n': 29466, 'mean_ms': 1512.8245, 'p50_ms': 149.9464, 'p90_ms': 5872.0256, 'p99_ms': 7952.4004, 'p99.9_ms': 8153.727, 'max_cum_ms': 8132.2612}
W release_lag_ns: {'n': 4601404, 'mean_ms': 19.8885, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 100.0734}
W holdback_wait_ns: {'n': 4484581, 'mean_ms': 20.3395, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 99.9923}
W release_processing_ns: {'n': 4601404, 'mean_ms': 0.0655, 'p50_ms': 0.0643, 'p90_ms': 0.0855, 'p99_ms': 0.1132, 'p99.9_ms': 0.1505, 'max_cum_ms': 2.0161}
W t_finalize_ns: {'n': 29460, 'mean_ms': 0.2651, 'p50_ms': 0.257, 'p90_ms': 0.3338, 'p99_ms': 0.3912, 'p99.9_ms': 0.4403, 'max_cum_ms': 1.3862}
W loop_lag_ns: {'n': 53982, 'mean_ms': 0.0723, 'p50_ms': 0.0, 'p90_ms': 0.1853, 'p99_ms': 1.0199, 'p99.9_ms': 1.5483, 'max_cum_ms': 12.4654}
W guard_windows_per_request: {'n': 29982, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_exec_ns: {'n': 29982, 'mean_ms': 3.5839, 'p50_ms': 4.2271, 'p90_ms': 4.4892, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 9.4148}
O guard_batch_windows: {'n': 29982, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_queue_ns: {'n': 29982, 'mean_ms': 0.1064, 'p50_ms': 0.1029, 'p90_ms': 0.1295, 'p99_ms': 0.1669, 'p99.9_ms': 0.5018, 'max_cum_ms': 4.4954}
worker counts: {'admitted': 29982, 'audit_enqueued': 59442, 'audit_written': 59442, 'background_round_trips': 10789, 'disposition_ALLOW': 29452, 'disposition_BLOCK': 530, 'guard_windows': 49408, 'lease_granted_tokens{org="org-a"}': 27939840, 'lease_refills': 136, 'provider_calls': 29452, 'provider_connections_opened': 5205, 'quota_admitted_tokens{org="org-a"}': 28405557, 'requests_by_round_trips{n="0"}': 29846, 'requests_by_round_trips{n="1"}': 136, 'shared_state_round_trips': 136}
owner counts: {'guard_batches': 29982, 'guard_windows': 49408, 'owner_requests': 29982, 'owner_windows': 49408}
gauges: {'audit_queue_bound': [181], 'guard_tokens_per_s': [25859.570924769152, 26277.94409965762], 'audit_completeness_ratio': [1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'input_queue_cap': [68.0, 69.0], 'guard_queue_cap_tokens': [25859.0, 26277.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 232736, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 232736.13832292237}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 236501, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 236501.49689691857}]
notes: []
