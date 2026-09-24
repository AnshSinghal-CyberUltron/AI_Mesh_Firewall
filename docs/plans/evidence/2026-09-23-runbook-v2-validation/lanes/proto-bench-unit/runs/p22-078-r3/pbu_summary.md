# p22-078-r3: strict FAIL | load-knee PASS (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 23400 (78.0/s) qualified 22974 (76.58/s) FP-blocks 410 (0.01752) expected-blocks 0 infra 16 (0.0006837606837606838) drops 0 safety 0 detection-misses 0
by class: {'benign': {'qualified': 22974, 'policy_block_fp': 410, 'infra_error': 16}}
infra reasons: {'http_503': 16, 'incomplete': 16, 'unjoined': 16, 'disposition_missing': 16, 'stage_canon_missing': 16, 'stage_det_missing': 16, 'stage_sem_missing': 16, 'stage_resolve_missing': 16, 'stage_dispatch_missing': 16, 'stage_out_missing': 16, 'stage_audit_missing': 16}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 16}

T_fw_addon: n=22974 p50=9.8381 p90=14.6865 p99=52.9028 p99.9=70.7215 max=74.0923 mean=12.0445
T_fw_addon_nohold: n=22974 p50=9.5967 p90=12.4546 p99=16.0177 p99.9=20.8143 max=43.0663 mean=9.0572
T_fw_addon_sse: n=16086 p50=9.9214 p90=30.4181 p99=53.9543 p99.9=70.9707 max=74.0923 mean=13.2908
T_fw_addon_json: n=6888 p50=9.645 p90=12.7268 p99=16.254 p99.9=20.9073 max=24.5324 mean=9.1341
T_addon_first_sse: n=16086 p50=9.5163 p90=12.8012 p99=27.7403 p99.9=33.6999 max=51.2636 mean=9.2546
T_addon_total_sse: n=16086 p50=9.5766 p90=12.3499 p99=15.8562 p99.9=20.522 max=43.0663 mean=9.0243
T_addon_total_json: n=6888 p50=9.645 p90=12.7268 p99=16.254 p99.9=20.9073 max=24.5324 mean=9.1341
T_release_lag_max: n=1578 p50=49.4175 p90=53.9726 p99=70.9807 p99.9=73.5565 max=74.0923 mean=48.8022
client_ttft_sse: n=16086 p50=159.5619 p90=162.8507 p99=177.7438 p99.9=183.73 max=201.2774 mean=159.3018
lateness: n=23400 p50=0.0841 p90=0.0938 p99=0.1048 p99.9=0.1237 max=0.2452 mean=0.0839
loadgen: [{'vm': 'rv-pbu-lg-1', 'busy_max': 8.1, 'busy_mean': 4.9, 'late_max_us': 614, 'conn_opens': 223, 'max_inflight': 211}, {'vm': 'rv-pbu-lg-2', 'busy_max': 5.1, 'busy_mean': 4.7, 'late_max_us': 245, 'conn_opens': 223, 'max_inflight': 211}]
wire per client request: {'client_to_gw': 8174.7, 'gw_to_client': 56379.6, 'gw_to_provider': 8833.3, 'provider_to_gw': 57049.3}
gateway cores 2.76 cpu-ms/req 35.497 (workers 31.436, owners 4.055, redis 0.189)
worker util {'n': 18, 'min': 0.087, 'median': 0.144, 'max': 0.166}; per-core schedstat max 0.139 mean 0.119; procstat max 0.252
gpu: {'0': {'samples': 299, 'sm_mean': 12.1, 'sm_max': 29.0, 'mem_mean': 2.2, 'fb_mb_max': 434.0}, '1': {'samples': 299, 'sm_mean': 11.6, 'sm_max': 29.0, 'mem_mean': 2.1, 'fb_mb_max': 434.0}}
rss max total by role (MB): {'redis': 1501.1, 'launcher': 59.7, 'owner': 3067.0, 'worker': 3667.3}; fds {'redis': 0, 'launcher': 7, 'owner': 126, 'worker': 1292}
W t_input_ns: {'n': 23362, 'mean_ms': 7.7332, 'p50_ms': 8.4541, 'p90_ms': 10.5513, 'p99_ms': 13.3038, 'p99.9_ms': 17.4326, 'max_cum_ms': 31.6019}
W t_admit_ns: {'n': 23378, 'mean_ms': 0.0905, 'p50_ms': 0.0865, 'p90_ms': 0.107, 'p99_ms': 0.1362, 'p99.9_ms': 0.5693, 'max_cum_ms': 3.9185}
W t_tokenize_ns: {'n': 23378, 'mean_ms': 2.9572, 'p50_ms': 2.9655, 'p90_ms': 4.5548, 'p99_ms': 5.3412, 'p99.9_ms': 6.3898, 'max_cum_ms': 8.0838}
W t_det_scan_ns: {'n': 23362, 'mean_ms': 0.1048, 'p50_ms': 0.1019, 'p90_ms': 0.1382, 'p99_ms': 0.1833, 'p99.9_ms': 0.2243, 'max_cum_ms': 24.0812}
W t_guard_wait_ns: {'n': 23362, 'mean_ms': 4.2262, 'p50_ms': 4.8169, 'p90_ms': 5.2101, 'p99_ms': 7.4383, 'p99.9_ms': 12.9106, 'max_cum_ms': 29.4642}
W guard_owner_rtt_ns: {'n': 23362, 'mean_ms': 4.4001, 'p50_ms': 5.0135, 'p90_ms': 5.4067, 'p99_ms': 7.7005, 'p99.9_ms': 11.9931, 'max_cum_ms': 28.5289}
W guard_queue_ns: {'n': 23362, 'mean_ms': 0.1098, 'p50_ms': 0.107, 'p90_ms': 0.1321, 'p99_ms': 0.1628, 'p99.9_ms': 0.938, 'max_cum_ms': 11.2781}
W guard_exec_ns: {'n': 23362, 'mean_ms': 3.5823, 'p50_ms': 4.2271, 'p90_ms': 4.4892, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 7.9147}
W dispatch_headers_ns: {'n': 22943, 'mean_ms': 1513.482, 'p50_ms': 149.9464, 'p90_ms': 5872.0256, 'p99_ms': 7952.4004, 'p99.9_ms': 8153.727, 'max_cum_ms': 8139.2743}
W release_lag_ns: {'n': 3572747, 'mean_ms': 19.8842, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 100.1673}
W holdback_wait_ns: {'n': 3481446, 'mean_ms': 20.3394, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 100.0223}
W release_processing_ns: {'n': 3572747, 'mean_ms': 0.0646, 'p50_ms': 0.0637, 'p90_ms': 0.0835, 'p99_ms': 0.107, 'p99.9_ms': 0.1362, 'max_cum_ms': 26.6219}
W t_finalize_ns: {'n': 22957, 'mean_ms': 0.2525, 'p50_ms': 0.2488, 'p90_ms': 0.3052, 'p99_ms': 0.3748, 'p99.9_ms': 0.4198, 'max_cum_ms': 2.7484}
W loop_lag_ns: {'n': 53580, 'mean_ms': 0.8216, 'p50_ms': 0.0097, 'p90_ms': 2.9, 'p99_ms': 8.2248, 'p99.9_ms': 10.027, 'max_cum_ms': 56.9536}
W guard_windows_per_request: {'n': 23378, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_exec_ns: {'n': 23371, 'mean_ms': 3.5826, 'p50_ms': 4.2271, 'p90_ms': 4.4892, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 7.9147}
O guard_batch_windows: {'n': 23371, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_queue_ns: {'n': 23371, 'mean_ms': 0.1098, 'p50_ms': 0.107, 'p90_ms': 0.1321, 'p99_ms': 0.1628, 'p99.9_ms': 0.938, 'max_cum_ms': 11.2781}
worker counts: {'admitted': 23378, 'audit_enqueued': 46319, 'audit_written': 46319, 'background_round_trips': 10716, 'disposition_ALLOW': 22952, 'disposition_BLOCK': 410, 'guard_windows': 38462, 'lease_granted_tokens{org="org-a"}': 21776640, 'lease_refills': 106, 'provider_calls': 22952, 'provider_connections_opened': 3253, 'quota_admitted_tokens{org="org-a"}': 22137129, 'requests_by_round_trips{n="0"}': 23272, 'requests_by_round_trips{n="1"}': 106, 'shared_state_round_trips': 106, 'shed{reason="guard_queue"}': 16}
owner counts: {'guard_batches': 23371, 'guard_windows': 38480, 'owner_requests': 23371, 'owner_windows': 38480}
gauges: {'audit_queue_bound': [181], 'audit_completeness_ratio': [1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'guard_tokens_per_s': [25768.91943081631, 26183.826731337853], 'input_queue_cap': [2.0], 'guard_queue_cap_tokens': [515.0, 523.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4638, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 231920.2748773468}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4713, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 235654.44058204067}]
notes: []
