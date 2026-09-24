# p22-078-r2: strict FAIL | load-knee PASS (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 23400 (78.0/s) qualified 22953 (76.51/s) FP-blocks 428 (0.01829) expected-blocks 0 infra 19 (0.000811965811965812) drops 0 safety 0 detection-misses 0
by class: {'benign': {'qualified': 22953, 'policy_block_fp': 428, 'infra_error': 19}}
infra reasons: {'http_503': 18, 'incomplete': 18, 'unjoined': 18, 'disposition_missing': 18, 'stage_canon_missing': 18, 'stage_det_missing': 18, 'stage_sem_missing': 18, 'stage_resolve_missing': 18, 'stage_dispatch_missing': 18, 'stage_out_missing': 18, 'stage_audit_missing': 18, 'block_on_unavailable_sem': 1}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 18, '403 http_403 type=policy_violation code=blocked_by_policy': 1}

T_fw_addon: n=22953 p50=9.839 p90=14.9448 p99=52.9385 p99.9=70.7274 max=87.9793 mean=12.2449
T_fw_addon_nohold: n=22953 p50=9.5586 p90=12.3912 p99=15.7279 p99.9=20.3126 max=44.4698 mean=9.0208
T_fw_addon_sse: n=16066 p50=9.9257 p90=35.1176 p99=53.964 p99.9=71.0131 max=87.9793 mean=13.5934
T_fw_addon_json: n=6887 p50=9.6372 p90=12.8088 p99=15.7717 p99.9=19.7838 max=24.7655 mean=9.0992
T_addon_first_sse: n=16066 p50=9.4809 p90=12.7953 p99=29.074 p99.9=33.8289 max=50.1619 mean=9.2485
T_addon_total_sse: n=16066 p50=9.5244 p90=12.2749 p99=15.6817 p99.9=20.377 max=44.4698 mean=8.9872
T_addon_total_json: n=6887 p50=9.6372 p90=12.8088 p99=15.7717 p99.9=19.7838 max=24.7655 mean=9.0992
T_release_lag_max: n=1700 p50=49.3756 p90=53.6817 p99=71.0014 p99.9=75.3314 max=87.9793 mean=48.8554
client_ttft_sse: n=16066 p50=159.5269 p90=162.8592 p99=179.1014 p99.9=183.8415 max=200.1947 mean=159.2966
lateness: n=23400 p50=0.0841 p90=0.0938 p99=0.1048 p99.9=0.1231 max=0.2777 mean=0.0839
loadgen: [{'vm': 'rv-pbu-lg-1', 'busy_max': 15.7, 'busy_mean': 4.9, 'late_max_us': 362, 'conn_opens': 221, 'max_inflight': 209}, {'vm': 'rv-pbu-lg-2', 'busy_max': 17.6, 'busy_mean': 4.7, 'late_max_us': 277, 'conn_opens': 223, 'max_inflight': 211}]
wire per client request: {'client_to_gw': 8160.4, 'gw_to_client': 56355.6, 'gw_to_provider': 8807.7, 'provider_to_gw': 57019.1}
gateway cores 2.754 cpu-ms/req 35.425 (workers 31.382, owners 4.037, redis 0.191)
worker util {'n': 18, 'min': 0.101, 'median': 0.139, 'max': 0.168}; per-core schedstat max 0.133 mean 0.118; procstat max 0.272
gpu: {'0': {'samples': 298, 'sm_mean': 11.4, 'sm_max': 30.0, 'mem_mean': 2.1, 'fb_mb_max': 434.0}, '1': {'samples': 298, 'sm_mean': 11.9, 'sm_max': 32.0, 'mem_mean': 2.2, 'fb_mb_max': 434.0}}
rss max total by role (MB): {'redis': 1340.2, 'launcher': 59.7, 'owner': 3067.0, 'worker': 3664.2}; fds {'redis': 0, 'launcher': 7, 'owner': 126, 'worker': 1286}
W t_input_ns: {'n': 23368, 'mean_ms': 7.7121, 'p50_ms': 8.3558, 'p90_ms': 10.5513, 'p99_ms': 13.1727, 'p99.9_ms': 17.4326, 'max_cum_ms': 31.6019}
W t_admit_ns: {'n': 23386, 'mean_ms': 0.0901, 'p50_ms': 0.0865, 'p90_ms': 0.106, 'p99_ms': 0.1362, 'p99.9_ms': 0.5693, 'max_cum_ms': 3.9185}
W t_tokenize_ns: {'n': 23386, 'mean_ms': 2.9519, 'p50_ms': 2.9655, 'p90_ms': 4.5548, 'p99_ms': 5.2756, 'p99.9_ms': 6.4553, 'max_cum_ms': 8.0838}
W t_det_scan_ns: {'n': 23368, 'mean_ms': 0.1046, 'p50_ms': 0.1019, 'p90_ms': 0.1382, 'p99_ms': 0.1833, 'p99.9_ms': 0.2161, 'max_cum_ms': 24.0812}
W t_guard_wait_ns: {'n': 23368, 'mean_ms': 4.2104, 'p50_ms': 4.8169, 'p90_ms': 5.2101, 'p99_ms': 7.4383, 'p99.9_ms': 12.3863, 'max_cum_ms': 29.4642}
W guard_owner_rtt_ns: {'n': 23368, 'mean_ms': 4.3838, 'p50_ms': 5.0135, 'p90_ms': 5.4067, 'p99_ms': 7.6349, 'p99.9_ms': 11.7309, 'max_cum_ms': 28.5289}
W guard_queue_ns: {'n': 23367, 'mean_ms': 0.1076, 'p50_ms': 0.105, 'p90_ms': 0.1306, 'p99_ms': 0.1608, 'p99.9_ms': 0.212, 'max_cum_ms': 11.2781}
W guard_exec_ns: {'n': 23367, 'mean_ms': 3.5721, 'p50_ms': 4.2271, 'p90_ms': 4.4892, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 7.9147}
W dispatch_headers_ns: {'n': 22933, 'mean_ms': 1514.6265, 'p50_ms': 149.9464, 'p90_ms': 5872.0256, 'p99_ms': 7952.4004, 'p99.9_ms': 8153.727, 'max_cum_ms': 8139.2743}
W release_lag_ns: {'n': 3571377, 'mean_ms': 19.8845, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 100.1673}
W holdback_wait_ns: {'n': 3480542, 'mean_ms': 20.3381, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 100.0223}
W release_processing_ns: {'n': 3571377, 'mean_ms': 0.0637, 'p50_ms': 0.0627, 'p90_ms': 0.0824, 'p99_ms': 0.107, 'p99.9_ms': 0.1362, 'max_cum_ms': 26.6219}
W t_finalize_ns: {'n': 22941, 'mean_ms': 0.2517, 'p50_ms': 0.2468, 'p90_ms': 0.3052, 'p99_ms': 0.3707, 'p99.9_ms': 0.4116, 'max_cum_ms': 2.7484}
W loop_lag_ns: {'n': 53590, 'mean_ms': 0.8224, 'p50_ms': 0.0075, 'p90_ms': 3.031, 'p99_ms': 8.2903, 'p99.9_ms': 10.5513, 'max_cum_ms': 56.9536}
W guard_windows_per_request: {'n': 23386, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_exec_ns: {'n': 23400, 'mean_ms': 3.5716, 'p50_ms': 4.2271, 'p90_ms': 4.4892, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 7.9147}
O guard_batch_windows: {'n': 23400, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_queue_ns: {'n': 23400, 'mean_ms': 0.1076, 'p50_ms': 0.105, 'p90_ms': 0.1306, 'p99_ms': 0.1608, 'p99.9_ms': 0.212, 'max_cum_ms': 11.2781}
worker counts: {'admitted': 23386, 'audit_enqueued': 46309, 'audit_written': 46309, 'background_round_trips': 10718, 'disposition_ALLOW': 22938, 'disposition_BLOCK': 430, 'guard_deadline_expired': 1, 'guard_unavailable_findings': 1, 'guard_windows': 38477, 'lease_granted_tokens{org="org-a"}': 22187520, 'lease_refills': 108, 'provider_calls': 22938, 'provider_connections_opened': 3186, 'quota_admitted_tokens{org="org-a"}': 22153128, 'requests_by_round_trips{n="0"}': 23278, 'requests_by_round_trips{n="1"}': 108, 'shared_state_round_trips': 108, 'shed{reason="guard_queue"}': 18}
owner counts: {'guard_batches': 23400, 'guard_deadline_expired': 1, 'guard_windows': 38527, 'owner_requests': 23400, 'owner_windows': 38526}
gauges: {'audit_queue_bound': [181], 'audit_completeness_ratio': [1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'guard_tokens_per_s': [25768.91943081631, 26183.826731337853], 'input_queue_cap': [2.0], 'guard_queue_cap_tokens': [515.0, 523.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4638, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 231920.2748773468}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4713, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 235654.44058204067}]
notes: []
