# w22-050: strict FAIL | load-knee FAIL (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 15000 (50.0/s) qualified 14609 (48.7/s) FP-blocks 387 (0.0258) expected-blocks 0 infra 4 (0.0002666666666666667) drops 0 safety 0 detection-misses 0
by class: {'benign': {'policy_block_fp': 387, 'qualified': 14609, 'infra_error': 4}}
infra reasons: {'http_503': 4, 'incomplete': 4, 'unjoined': 4, 'disposition_missing': 4, 'stage_canon_missing': 4, 'stage_det_missing': 4, 'stage_sem_missing': 4, 'stage_resolve_missing': 4, 'stage_dispatch_missing': 4, 'stage_out_missing': 4, 'stage_audit_missing': 4}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 4}

T_fw_addon: n=14609 p50=15.2168 p90=18.7175 p99=59.3385 p99.9=75.4952 max=82.7331 mean=18.4927
T_fw_addon_nohold: n=14609 p50=14.9658 p90=15.9723 p99=20.0681 p99.9=23.3244 max=28.516 mean=15.0564
T_fw_addon_sse: n=10227 p50=15.279 p90=53.0472 p99=73.2287 p99.9=75.6208 max=82.7331 mean=19.914
T_fw_addon_json: n=4382 p50=15.0864 p90=16.0508 p99=20.233 p99.9=23.4357 max=27.2183 mean=15.1756
T_addon_first_sse: n=10227 p50=14.8541 p90=16.1311 p99=34.4641 p99.9=37.5346 max=54.7729 mean=15.2427
T_addon_total_sse: n=10227 p50=14.9162 p90=15.9291 p99=19.9028 p99.9=23.2756 max=28.516 mean=15.0053
T_addon_total_json: n=4382 p50=15.0864 p90=16.0508 p99=20.233 p99.9=23.4357 max=27.2183 mean=15.1756
T_release_lag_max: n=1035 p50=55.6989 p90=73.2147 p99=75.6208 p99.9=81.8396 max=82.7331 mean=57.8906
client_ttft_sse: n=10227 p50=164.8949 p90=166.1698 p99=184.4878 p99.9=187.5475 max=204.8227 mean=165.2824
lateness: n=15000 p50=0.0874 p90=0.1004 p99=0.1162 p99.9=0.1463 max=0.3438 mean=0.0875
loadgen: [{'vm': 'rv-pbu-lg-1', 'busy_max': 15.8, 'busy_mean': 5.7, 'late_max_us': 343, 'conn_opens': 204, 'max_inflight': 203}, {'vm': 'rv-pbu-lg-2', 'busy_max': 18.2, 'busy_mean': 5.5, 'late_max_us': 343, 'conn_opens': 204, 'max_inflight': 203}]
wire per client request: {'client_to_gw': 14028.9, 'gw_to_client': 98116.9, 'gw_to_provider': 13233.3, 'provider_to_gw': 99476.7}
gateway cores 2.661 cpu-ms/req 53.406 (workers 46.506, owners 6.891, redis 0.213)
worker util {'n': 18, 'min': 0.096, 'median': 0.13, 'max': 0.163}; per-core schedstat max 0.134 mean 0.113; procstat max 0.131
gpu: {'0': {'samples': 298, 'sm_mean': 13.0, 'sm_max': 28.0, 'mem_mean': 2.3, 'fb_mb_max': 434.0}, '1': {'samples': 298, 'sm_mean': 14.7, 'sm_max': 28.0, 'mem_mean': 2.7, 'fb_mb_max': 434.0}}
rss max total by role (MB): {'redis': 2594.9, 'launcher': 59.7, 'owner': 3067.2, 'worker': 3693.6}; fds {'redis': 0, 'launcher': 7, 'owner': 126, 'worker': 1262}
W t_input_ns: {'n': 14980, 'mean_ms': 13.1235, 'p50_ms': 13.1727, 'p90_ms': 13.697, 'p99_ms': 15.2699, 'p99.9_ms': 20.5783, 'max_cum_ms': 32.2244}
W t_admit_ns: {'n': 14984, 'mean_ms': 0.0958, 'p50_ms': 0.0886, 'p90_ms': 0.1091, 'p99_ms': 0.1731, 'p99.9_ms': 0.5018, 'max_cum_ms': 3.9185}
W t_tokenize_ns: {'n': 14984, 'mean_ms': 5.4972, 'p50_ms': 5.6033, 'p90_ms': 6.0621, 'p99_ms': 6.5208, 'p99.9_ms': 7.6349, 'max_cum_ms': 8.3237}
W t_det_scan_ns: {'n': 14980, 'mean_ms': 0.1338, 'p50_ms': 0.1275, 'p90_ms': 0.1567, 'p99_ms': 0.1976, 'p99.9_ms': 0.2243, 'max_cum_ms': 24.0812}
W t_guard_wait_ns: {'n': 14980, 'mean_ms': 7.0078, 'p50_ms': 6.914, 'p90_ms': 7.1107, 'p99_ms': 8.5852, 'p99.9_ms': 14.0902, 'max_cum_ms': 29.4642}
W guard_owner_rtt_ns: {'n': 14980, 'mean_ms': 7.2539, 'p50_ms': 7.1762, 'p90_ms': 7.3728, 'p99_ms': 8.4541, 'p99.9_ms': 13.3038, 'max_cum_ms': 28.5289}
W guard_queue_ns: {'n': 14980, 'mean_ms': 0.1186, 'p50_ms': 0.1162, 'p90_ms': 0.1382, 'p99_ms': 0.1649, 'p99.9_ms': 0.2161, 'max_cum_ms': 12.2232}
W guard_exec_ns: {'n': 14980, 'mean_ms': 6.3753, 'p50_ms': 6.3898, 'p90_ms': 6.4553, 'p99_ms': 6.6519, 'p99.9_ms': 7.1762, 'max_cum_ms': 8.4399}
W dispatch_headers_ns: {'n': 14607, 'mean_ms': 2547.8864, 'p50_ms': 152.0435, 'p90_ms': 8153.727, 'p99_ms': 8153.727, 'p99.9_ms': 8153.727, 'max_cum_ms': 8151.0442}
W release_lag_ns: {'n': 4031498, 'mean_ms': 19.9615, 'p50_ms': 20.054, 'p90_ms': 20.5783, 'p99_ms': 40.108, 'p99.9_ms': 42.2052, 'max_cum_ms': 150.0061}
W holdback_wait_ns: {'n': 3944212, 'mean_ms': 20.3418, 'p50_ms': 20.054, 'p90_ms': 20.3162, 'p99_ms': 40.108, 'p99.9_ms': 42.2052, 'max_cum_ms': 149.9429}
W release_processing_ns: {'n': 4031498, 'mean_ms': 0.0601, 'p50_ms': 0.0571, 'p90_ms': 0.0886, 'p99_ms': 0.1213, 'p99.9_ms': 0.1567, 'max_cum_ms': 26.6219}
W t_finalize_ns: {'n': 14605, 'mean_ms': 0.3092, 'p50_ms': 0.3092, 'p90_ms': 0.3912, 'p99_ms': 0.4526, 'p99.9_ms': 0.5018, 'max_cum_ms': 2.7484}
W loop_lag_ns: {'n': 53580, 'mean_ms': 0.8175, 'p50_ms': 0.1203, 'p90_ms': 1.4828, 'p99_ms': 8.2248, 'p99.9_ms': 10.8134, 'max_cum_ms': 56.9536}
W guard_windows_per_request: {'n': 14984, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_exec_ns: {'n': 14985, 'mean_ms': 6.3753, 'p50_ms': 6.3898, 'p90_ms': 6.4553, 'p99_ms': 6.6519, 'p99.9_ms': 7.1762, 'max_cum_ms': 8.4399}
O guard_batch_windows: {'n': 14985, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_queue_ns: {'n': 14985, 'mean_ms': 0.1186, 'p50_ms': 0.1162, 'p90_ms': 0.1382, 'p99_ms': 0.1649, 'p99.9_ms': 0.2161, 'max_cum_ms': 12.2232}
worker counts: {'admitted': 14984, 'audit_enqueued': 29585, 'audit_written': 29585, 'background_round_trips': 10716, 'disposition_ALLOW': 14594, 'disposition_BLOCK': 386, 'guard_windows': 44940, 'lease_granted_tokens{org="org-a"}': 25063680, 'lease_refills': 122, 'provider_calls': 14594, 'provider_connections_opened': 964, 'quota_admitted_tokens{org="org-a"}': 25158183, 'requests_by_round_trips{n="0"}': 14862, 'requests_by_round_trips{n="1"}': 122, 'shared_state_round_trips': 122, 'shed{reason="guard_queue"}': 4}
owner counts: {'guard_batches': 14985, 'guard_windows': 44955, 'owner_requests': 14985, 'owner_windows': 44955}
gauges: {'audit_queue_bound': [181], 'audit_completeness_ratio': [1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'guard_tokens_per_s': [25768.91943081631, 26183.826731337853], 'input_queue_cap': [2.0], 'guard_queue_cap_tokens': [515.0, 523.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4638, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 231920.2748773468}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4713, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 235654.44058204067}]
notes: []
