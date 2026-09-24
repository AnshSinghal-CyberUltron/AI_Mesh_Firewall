# l22-100: strict FAIL | load-knee PASS (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 60000 (100.0/s) qualified 59423 (99.04/s) FP-blocks 551 (0.00918) expected-blocks 0 infra 26 (0.0004333333333333333) drops 0 safety 0 detection-misses 0
by class: {'benign': {'policy_block_fp': 551, 'infra_error': 26, 'qualified': 59423}}
infra reasons: {'http_503': 26, 'incomplete': 26, 'unjoined': 26, 'disposition_missing': 26, 'stage_canon_missing': 26, 'stage_det_missing': 26, 'stage_sem_missing': 26, 'stage_resolve_missing': 26, 'stage_dispatch_missing': 26, 'stage_out_missing': 26, 'stage_audit_missing': 26}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 26}

T_fw_addon: n=59423 p50=7.881 p90=64.5617 p99=94.8207 p99.9=99.3196 max=157.0822 mean=14.4924
T_fw_addon_nohold: n=59423 p50=7.4377 p90=9.2765 p99=16.3239 p99.9=21.1683 max=29.5932 mean=7.5366
T_fw_addon_sse: n=59423 p50=7.881 p90=64.5617 p99=94.8207 p99.9=99.3196 max=157.0822 mean=14.4924
T_fw_addon_json: n=0
T_addon_first_sse: n=59423 p50=7.2876 p90=9.3982 p99=36.7171 p99.9=39.7469 max=69.3828 mean=7.896
T_addon_total_sse: n=59423 p50=7.4377 p90=9.2765 p99=16.3239 p99.9=21.1683 max=29.5932 mean=7.5366
T_addon_total_json: n=0
T_release_lag_max: n=5937 p50=67.9139 p90=94.8217 p99=99.3196 p99.9=127.6947 max=157.0822 mean=71.0149
client_ttft_sse: n=59423 p50=157.3227 p90=159.4391 p99=186.7505 p99.9=189.7863 max=219.4501 mean=157.9335
lateness: n=60000 p50=0.0856 p90=0.095 p99=0.1063 p99.9=0.1222 max=0.3018 mean=0.0829
loadgen: [{'vm': 'rv-pbu-lg-1', 'busy_max': 22.8, 'busy_mean': 9.7, 'late_max_us': 711, 'conn_opens': 614, 'max_inflight': 608}, {'vm': 'rv-pbu-lg-2', 'busy_max': 22.5, 'busy_mean': 9.3, 'late_max_us': 301, 'conn_opens': 610, 'max_inflight': 607}]
wire per client request: {'client_to_gw': 19457.1, 'gw_to_client': 141301.4, 'gw_to_provider': 14185.2, 'provider_to_gw': 143335.6}
gateway cores 6.697 cpu-ms/req 67.082 (workers 64.37, owners 2.707, redis 0.209)
worker util {'n': 18, 'min': 0.257, 'median': 0.359, 'max': 0.41}; per-core schedstat max 0.317 mean 0.283; procstat max 0.347
gpu: {'0': {'samples': 595, 'sm_mean': 8.8, 'sm_max': 17.0, 'mem_mean': 1.6, 'fb_mb_max': 434.0}, '1': {'samples': 595, 'sm_mean': 9.5, 'sm_max': 18.0, 'mem_mean': 1.7, 'fb_mb_max': 434.0}}
rss max total by role (MB): {'redis': 2438.7, 'launcher': 59.7, 'owner': 3067.1, 'worker': 3689.0}; fds {'redis': 0, 'launcher': 7, 'owner': 129, 'worker': 2879}
W t_input_ns: {'n': 59941, 'mean_ms': 5.302, 'p50_ms': 5.3412, 'p90_ms': 6.1932, 'p99_ms': 8.2903, 'p99.9_ms': 17.6947, 'max_cum_ms': 32.2244}
W t_admit_ns: {'n': 59968, 'mean_ms': 0.0881, 'p50_ms': 0.0855, 'p90_ms': 0.1009, 'p99_ms': 0.1254, 'p99.9_ms': 0.5284, 'max_cum_ms': 3.9185}
W t_tokenize_ns: {'n': 59968, 'mean_ms': 1.6445, 'p50_ms': 1.6302, 'p90_ms': 2.3101, 'p99_ms': 2.6051, 'p99.9_ms': 3.7192, 'max_cum_ms': 8.3237}
W t_det_scan_ns: {'n': 59942, 'mean_ms': 0.0795, 'p50_ms': 0.0783, 'p90_ms': 0.0906, 'p99_ms': 0.1183, 'p99.9_ms': 0.1526, 'max_cum_ms': 24.0812}
W t_guard_wait_ns: {'n': 59941, 'mean_ms': 3.1322, 'p50_ms': 2.9655, 'p90_ms': 3.5553, 'p99_ms': 4.8824, 'p99.9_ms': 15.2699, 'max_cum_ms': 29.4642}
W guard_owner_rtt_ns: {'n': 59941, 'mean_ms': 3.1455, 'p50_ms': 3.0638, 'p90_ms': 3.457, 'p99_ms': 4.4237, 'p99.9_ms': 12.5174, 'max_cum_ms': 28.5289}
W guard_queue_ns: {'n': 59941, 'mean_ms': 0.1028, 'p50_ms': 0.0988, 'p90_ms': 0.1152, 'p99_ms': 0.1382, 'p99.9_ms': 1.4991, 'max_cum_ms': 12.2232}
W guard_exec_ns: {'n': 59941, 'mean_ms': 2.2419, 'p50_ms': 2.2446, 'p90_ms': 2.3101, 'p99_ms': 2.4084, 'p99.9_ms': 2.7361, 'max_cum_ms': 7.9147}
W dispatch_headers_ns: {'n': 59407, 'mean_ms': 151.5232, 'p50_ms': 152.0435, 'p90_ms': 154.1407, 'p99_ms': 154.1407, 'p99.9_ms': 160.4321, 'max_cum_ms': 8139.2743}
W release_lag_ns: {'n': 23426434, 'mean_ms': 29.9179, 'p50_ms': 30.0155, 'p90_ms': 30.2776, 'p99_ms': 60.031, 'p99.9_ms': 60.5553, 'max_cum_ms': 150.0061}
W holdback_wait_ns: {'n': 22918686, 'mean_ms': 30.5116, 'p50_ms': 30.0155, 'p90_ms': 30.2776, 'p99_ms': 60.031, 'p99.9_ms': 60.5553, 'max_cum_ms': 149.9429}
W release_processing_ns: {'n': 23426434, 'mean_ms': 0.0676, 'p50_ms': 0.0648, 'p90_ms': 0.0947, 'p99_ms': 0.1321, 'p99.9_ms': 0.1669, 'max_cum_ms': 26.6219}
W t_finalize_ns: {'n': 59409, 'mean_ms': 0.336, 'p50_ms': 0.3379, 'p90_ms': 0.4157, 'p99_ms': 0.4731, 'p99.9_ms': 0.5222, 'max_cum_ms': 2.7484}
W loop_lag_ns: {'n': 107030, 'mean_ms': 0.9937, 'p50_ms': 0.1444, 'p90_ms': 2.8017, 'p99_ms': 9.8959, 'p99.9_ms': 12.3863, 'max_cum_ms': 56.9536}
W guard_windows_per_request: {'n': 59968, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_exec_ns: {'n': 59957, 'mean_ms': 2.2419, 'p50_ms': 2.2446, 'p90_ms': 2.3101, 'p99_ms': 2.4084, 'p99.9_ms': 2.7361, 'max_cum_ms': 7.9147}
O guard_batch_windows: {'n': 59957, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_queue_ns: {'n': 59957, 'mean_ms': 0.1028, 'p50_ms': 0.0988, 'p90_ms': 0.1152, 'p99_ms': 0.1382, 'p99.9_ms': 1.4991, 'max_cum_ms': 12.2232}
worker counts: {'admitted': 59968, 'audit_enqueued': 119350, 'audit_written': 119350, 'background_round_trips': 21406, 'disposition_ALLOW': 59390, 'disposition_BLOCK': 551, 'guard_windows': 59941, 'lease_granted_tokens{org="org-a"}': 46840320, 'lease_refills': 228, 'provider_calls': 59390, 'provider_connections_opened': 7387, 'quota_admitted_tokens{org="org-a"}': 47825308, 'requests_by_round_trips{n="0"}': 59740, 'requests_by_round_trips{n="1"}': 228, 'shared_state_round_trips': 228, 'shed{reason="guard_queue"}': 26}
owner counts: {'guard_batches': 59957, 'guard_windows': 59957, 'owner_requests': 59957, 'owner_windows': 59957}
gauges: {'audit_queue_bound': [181], 'audit_completeness_ratio': [1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'guard_tokens_per_s': [25768.91943081631, 26183.826731337853], 'input_queue_cap': [2.0], 'guard_queue_cap_tokens': [515.0, 523.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4638, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 231920.2748773468}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4713, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 235654.44058204067}]
notes: []
