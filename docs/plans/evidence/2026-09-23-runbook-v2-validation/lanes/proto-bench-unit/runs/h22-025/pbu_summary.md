# h22-025: strict FAIL | load-knee PASS (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 7500 (25.0/s) qualified 7392 (24.64/s) FP-blocks 101 (0.01347) expected-blocks 0 infra 7 (0.0009333333333333333) drops 0 safety 0 detection-misses 0
by class: {'benign': {'qualified': 7392, 'policy_block_fp': 101, 'infra_error': 7}}
infra reasons: {'http_503': 4, 'incomplete': 4, 'unjoined': 4, 'disposition_missing': 4, 'stage_canon_missing': 4, 'stage_det_missing': 4, 'stage_sem_missing': 4, 'stage_resolve_missing': 4, 'stage_dispatch_missing': 4, 'stage_out_missing': 4, 'stage_audit_missing': 4, 'block_on_unavailable_sem': 3}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 4, '403 http_403 type=policy_violation code=blocked_by_policy': 3}

T_fw_addon: n=7392 p50=9.6953 p90=13.7314 p99=51.9435 p99.9=70.0984 max=72.9708 mean=11.7669
T_fw_addon_nohold: n=7392 p50=9.4541 p90=11.708 p99=14.1715 p99.9=18.6387 max=20.7925 mean=8.8171
T_fw_addon_sse: n=5169 p50=9.7671 p90=29.6988 p99=52.9033 p99.9=70.2608 max=72.9708 mean=13.0149
T_fw_addon_json: n=2223 p50=9.5451 p90=11.8395 p99=14.1043 p99.9=16.8376 max=17.1383 mean=8.8648
T_addon_first_sse: n=5169 p50=9.3787 p90=12.7128 p99=26.6517 p99.9=33.6226 max=49.8712 mean=9.0716
T_addon_total_sse: n=5169 p50=9.4236 p90=11.6483 p99=14.2448 p99.9=19.1611 max=20.7925 mean=8.7965
T_addon_total_json: n=2223 p50=9.5451 p90=11.8395 p99=14.1043 p99.9=16.8376 max=17.1383 mean=8.8648
T_release_lag_max: n=498 p50=49.2588 p90=52.9219 p99=70.363 p99.9=72.9708 max=72.9708 mean=48.6438
client_ttft_sse: n=5169 p50=159.4243 p90=162.754 p99=176.7285 p99.9=183.6507 max=199.8796 mean=159.1194
lateness: n=7500 p50=0.0924 p90=0.1084 p99=0.1254 p99.9=0.1438 max=0.1779 mean=0.0932
loadgen: [{'vm': 'rv-pbu-lg-1', 'busy_max': 7.1, 'busy_mean': 4.1, 'late_max_us': 346, 'conn_opens': 74, 'max_inflight': 70}, {'vm': 'rv-pbu-lg-2', 'busy_max': 4.4, 'busy_mean': 4.0, 'late_max_us': 320, 'conn_opens': 74, 'max_inflight': 70}]
wire per client request: {'client_to_gw': 9198.9, 'gw_to_client': 56863.1, 'gw_to_provider': 9768.2, 'provider_to_gw': 57510.9}
gateway cores 0.975 cpu-ms/req 39.129 (workers 34.909, owners 4.202, redis 0.288)
worker util {'n': 18, 'min': 0.027, 'median': 0.051, 'max': 0.063}; per-core schedstat max 0.054 mean 0.044; procstat max 0.074
gpu: {'0': {'samples': 299, 'sm_mean': 3.6, 'sm_max': 12.0, 'mem_mean': 0.6, 'fb_mb_max': 434.0}, '1': {'samples': 299, 'sm_mean': 3.6, 'sm_max': 13.0, 'mem_mean': 0.6, 'fb_mb_max': 434.0}}
rss max total by role (MB): {'redis': 71.8, 'launcher': 59.3, 'owner': 3065.4, 'worker': 3613.6}; fds {'redis': 0, 'launcher': 7, 'owner': 129, 'worker': 729}
W t_input_ns: {'n': 7494, 'mean_ms': 7.6552, 'p50_ms': 8.2903, 'p90_ms': 10.027, 'p99_ms': 12.3863, 'p99.9_ms': 17.1704, 'max_cum_ms': 381.0444}
W t_admit_ns: {'n': 7498, 'mean_ms': 0.0951, 'p50_ms': 0.0906, 'p90_ms': 0.1121, 'p99_ms': 0.1464, 'p99.9_ms': 0.6431, 'max_cum_ms': 2.84}
W t_tokenize_ns: {'n': 7498, 'mean_ms': 2.7292, 'p50_ms': 2.7689, 'p90_ms': 4.1452, 'p99_ms': 4.6203, 'p99.9_ms': 6.0621, 'max_cum_ms': 7.1111}
W t_det_scan_ns: {'n': 7494, 'mean_ms': 0.0993, 'p50_ms': 0.1009, 'p90_ms': 0.1213, 'p99_ms': 0.1485, 'p99.9_ms': 0.1792, 'max_cum_ms': 0.6194}
W t_guard_wait_ns: {'n': 7494, 'mean_ms': 4.3806, 'p50_ms': 4.8169, 'p90_ms': 5.2101, 'p99_ms': 7.2417, 'p99.9_ms': 12.3863, 'max_cum_ms': 376.1411}
W guard_owner_rtt_ns: {'n': 7494, 'mean_ms': 4.5539, 'p50_ms': 5.0135, 'p90_ms': 5.4067, 'p99_ms': 7.4383, 'p99.9_ms': 10.5513, 'max_cum_ms': 376.3447}
W guard_queue_ns: {'n': 7491, 'mean_ms': 0.1136, 'p50_ms': 0.1121, 'p90_ms': 0.1285, 'p99_ms': 0.1505, 'p99.9_ms': 0.1874, 'max_cum_ms': 0.8683}
W guard_exec_ns: {'n': 7491, 'mean_ms': 3.6383, 'p50_ms': 4.2926, 'p90_ms': 4.4892, 'p99_ms': 6.5208, 'p99.9_ms': 6.6519, 'max_cum_ms': 9.0492}
W dispatch_headers_ns: {'n': 7400, 'mean_ms': 1542.3826, 'p50_ms': 149.9464, 'p90_ms': 5939.1345, 'p99_ms': 7885.2915, 'p99.9_ms': 8153.727, 'max_cum_ms': 8131.3185}
W release_lag_ns: {'n': 1155784, 'mean_ms': 19.8876, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 80.1119}
W holdback_wait_ns: {'n': 1126548, 'mean_ms': 20.3373, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 80.0653}
W release_processing_ns: {'n': 1155784, 'mean_ms': 0.0647, 'p50_ms': 0.0643, 'p90_ms': 0.0835, 'p99_ms': 0.1091, 'p99.9_ms': 0.1403, 'max_cum_ms': 0.6632}
W t_finalize_ns: {'n': 7400, 'mean_ms': 0.2557, 'p50_ms': 0.2509, 'p90_ms': 0.3092, 'p99_ms': 0.3666, 'p99.9_ms': 0.4116, 'max_cum_ms': 0.4595}
W loop_lag_ns: {'n': 53670, 'mean_ms': 0.657, 'p50_ms': 0.028, 'p90_ms': 1.1715, 'p99_ms': 6.3242, 'p99.9_ms': 8.4541, 'max_cum_ms': 11.9257}
W guard_windows_per_request: {'n': 7498, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_exec_ns: {'n': 7482, 'mean_ms': 3.6375, 'p50_ms': 4.2926, 'p90_ms': 4.4892, 'p99_ms': 6.5208, 'p99.9_ms': 6.6519, 'max_cum_ms': 9.0492}
O guard_batch_windows: {'n': 7482, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_queue_ns: {'n': 7482, 'mean_ms': 0.1136, 'p50_ms': 0.1121, 'p90_ms': 0.1285, 'p99_ms': 0.1505, 'p99.9_ms': 0.1874, 'max_cum_ms': 0.8683}
worker counts: {'admitted': 7498, 'audit_enqueued': 14894, 'audit_written': 14894, 'background_round_trips': 10734, 'disposition_ALLOW': 7390, 'disposition_BLOCK': 104, 'guard_deadline_expired': 3, 'guard_unavailable_findings': 3, 'guard_windows': 12357, 'lease_granted_tokens{org="org-a"}': 6779520, 'lease_refills': 33, 'provider_calls': 7390, 'provider_connections_opened': 1155, 'quota_admitted_tokens{org="org-a"}': 7110382, 'requests_by_round_trips{n="0"}': 7465, 'requests_by_round_trips{n="1"}': 33, 'shared_state_round_trips': 33, 'shed{reason="guard_queue"}': 4}
owner counts: {'guard_batches': 7482, 'guard_deadline_expired': 3, 'guard_windows': 12339, 'owner_requests': 7484, 'owner_windows': 12342}
gauges: {'audit_queue_bound': [181], 'audit_completeness_ratio': [1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'guard_tokens_per_s': [26778.36769188868, 26979.31316245365], 'input_queue_cap': [2.0], 'guard_queue_cap_tokens': [535.0, 539.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4820, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 241005.30922699813}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4856, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 242813.81846208285}]
notes: []
