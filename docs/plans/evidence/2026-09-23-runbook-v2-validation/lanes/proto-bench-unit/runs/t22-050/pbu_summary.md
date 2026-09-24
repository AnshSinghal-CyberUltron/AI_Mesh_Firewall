# t22-050: strict FAIL | load-knee PASS (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 15000 (50.0/s) qualified 14750 (49.17/s) FP-blocks 246 (0.0164) expected-blocks 0 infra 4 (0.0002666666666666667) drops 0 safety 0 detection-misses 0
by class: {'benign': {'policy_block_fp': 246, 'qualified': 14750, 'infra_error': 4}}
infra reasons: {'http_503': 3, 'incomplete': 3, 'unjoined': 3, 'disposition_missing': 3, 'stage_canon_missing': 3, 'stage_det_missing': 3, 'stage_sem_missing': 3, 'stage_resolve_missing': 3, 'stage_dispatch_missing': 3, 'stage_out_missing': 3, 'stage_audit_missing': 3, 'block_on_unavailable_sem': 1}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 3, '403 http_403 type=policy_violation code=blocked_by_policy': 1}

T_fw_addon: n=14750 p50=13.628 p90=18.9365 p99=57.1671 p99.9=74.6666 max=78.8447 mean=16.5833
T_fw_addon_nohold: n=14750 p50=13.3089 p90=16.7701 p99=19.3527 p99.9=23.8829 max=31.3865 mean=13.4249
T_fw_addon_sse: n=10322 p50=13.767 p90=37.2459 p99=58.0677 p99.9=75.5784 max=78.8447 mean=17.9078
T_fw_addon_json: n=4428 p50=13.4007 p90=16.8718 p99=19.3311 p99.9=23.6133 max=31.3865 mean=13.4959
T_addon_first_sse: n=10322 p50=13.1912 p90=17.0966 p99=32.7776 p99.9=37.4777 max=53.1637 mean=13.6126
T_addon_total_sse: n=10322 p50=13.2781 p90=16.6989 p99=19.3857 p99.9=23.8829 max=28.2034 mean=13.3944
T_addon_total_json: n=4428 p50=13.4007 p90=16.8718 p99=19.3311 p99.9=23.6133 max=31.3865 mean=13.4959
T_release_lag_max: n=1059 p50=53.1316 p90=58.0571 p99=75.5784 p99.9=78.3256 max=78.8447 mean=53.2111
client_ttft_sse: n=10322 p50=163.2279 p90=167.1389 p99=182.792 p99.9=187.5772 max=203.2267 mean=163.6576
lateness: n=15000 p50=0.0841 p90=0.0961 p99=0.1131 p99.9=0.2078 max=0.2682 mean=0.0854
loadgen: [{'vm': 'rv-pbu-lg-1', 'busy_max': 17.9, 'busy_mean': 4.5, 'late_max_us': 441, 'conn_opens': 148, 'max_inflight': 136}, {'vm': 'rv-pbu-lg-2', 'busy_max': 17.8, 'busy_mean': 4.4, 'late_max_us': 268, 'conn_opens': 149, 'max_inflight': 137}]
wire per client request: {'client_to_gw': 8415.5, 'gw_to_client': 56349.1, 'gw_to_provider': 9408.8, 'provider_to_gw': 57007.8}
gateway cores 1.743 cpu-ms/req 34.984 (workers 34.977, owners 0.0, redis 0.217)
worker util {'n': 18, 'min': 0.057, 'median': 0.102, 'max': 0.157}; per-core schedstat max 0.104 mean 0.087; procstat max 0.195
gpu: {'0': {'samples': 298, 'sm_mean': 9.4, 'sm_max': 21.0, 'mem_mean': 5.4, 'fb_mb_max': 2206.0}, '1': {'samples': 298, 'sm_mean': 10.2, 'sm_max': 19.0, 'mem_mean': 5.9, 'fb_mb_max': 2206.0}}
rss max total by role (MB): {'redis': 194.3, 'triton': 1012.7, 'launcher': 26.2, 'worker': 3908.5}; fds {'redis': 0, 'triton': 0, 'launcher': 3, 'worker': 1104}
W t_input_ns: {'n': 14980, 'mean_ms': 11.9614, 'p50_ms': 11.862, 'p90_ms': 14.2213, 'p99_ms': 17.6947, 'p99.9_ms': 21.1026, 'max_cum_ms': 27.5786}
W t_admit_ns: {'n': 14983, 'mean_ms': 0.0959, 'p50_ms': 0.0916, 'p90_ms': 0.1121, 'p99_ms': 0.1485, 'p99.9_ms': 0.684, 'max_cum_ms': 2.7753}
W t_tokenize_ns: {'n': 14983, 'mean_ms': 3.3011, 'p50_ms': 3.3587, 'p90_ms': 5.079, 'p99_ms': 5.7999, 'p99.9_ms': 6.4553, 'max_cum_ms': 7.6231}
W t_det_scan_ns: {'n': 14980, 'mean_ms': 0.1121, 'p50_ms': 0.1121, 'p90_ms': 0.1423, 'p99_ms': 0.1812, 'p99.9_ms': 0.214, 'max_cum_ms': 2.1254}
W t_guard_wait_ns: {'n': 14980, 'mean_ms': 8.177, 'p50_ms': 8.0282, 'p90_ms': 9.1095, 'p99_ms': 11.7309, 'p99.9_ms': 15.401, 'max_cum_ms': 21.0018}
W dispatch_headers_ns: {'n': 14745, 'mean_ms': 1521.0183, 'p50_ms': 149.9464, 'p90_ms': 5872.0256, 'p99_ms': 7885.2915, 'p99.9_ms': 8153.727, 'max_cum_ms': 8133.3319}
W release_lag_ns: {'n': 2288641, 'mean_ms': 19.8872, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.6323, 'max_cum_ms': 100.1479}
W holdback_wait_ns: {'n': 2230453, 'mean_ms': 20.3383, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.6323, 'max_cum_ms': 100.0657}
W release_processing_ns: {'n': 2288641, 'mean_ms': 0.066, 'p50_ms': 0.0653, 'p90_ms': 0.0876, 'p99_ms': 0.1162, 'p99.9_ms': 0.1485, 'max_cum_ms': 1.9494}
W t_finalize_ns: {'n': 14754, 'mean_ms': 0.2698, 'p50_ms': 0.2642, 'p90_ms': 0.3379, 'p99_ms': 0.3994, 'p99.9_ms': 0.4485, 'max_cum_ms': 0.6614}
W loop_lag_ns: {'n': 53640, 'mean_ms': 0.6709, 'p50_ms': 0.0219, 'p90_ms': 1.1551, 'p99_ms': 6.8485, 'p99.9_ms': 9.6338, 'max_cum_ms': 12.6514}
W guard_windows_per_request: {'n': 14983, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
worker counts: {'admitted': 14983, 'audit_enqueued': 29734, 'audit_written': 29734, 'background_round_trips': 10728, 'disposition_ALLOW': 14734, 'disposition_BLOCK': 246, 'guard_rpc_errors': 1, 'guard_unavailable_findings': 1, 'lease_granted_tokens{org="org-a"}': 14380800, 'lease_refills': 70, 'provider_calls': 14734, 'provider_connections_opened': 2300, 'quota_admitted_tokens{org="org-a"}': 14216030, 'requests_by_round_trips{n="0"}': 14913, 'requests_by_round_trips{n="1"}': 70, 'shared_state_round_trips': 70, 'shed{reason="guard_queue"}': 3}
owner counts: {}
gauges: {'audit_queue_bound': [181], 'audit_completeness_ratio': [1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'guard_tokens_per_s': [21756.87349732643, 21757.40563756465, 21758.67455252781, 22065.79461432838, 22745.666213816774, 22746.477594264124, 23857.713980046217, 24374.243606209155, 24499.83868671323, 25244.08321706887, 25647.818393789425, 26012.760340266355, 28099.377478703507, 28467.725063403173, 30553.201907293103, 32332.361807941918, 57971.41004325191, 63307.11989407819], 'input_queue_cap': [2.0, 4.0], 'guard_queue_cap_tokens': [435.0, 441.0, 454.0, 477.0, 487.0, 489.0, 504.0, 512.0, 520.0, 561.0, 569.0, 611.0, 646.0, 1159.0, 1266.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 1839, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 91950.54475029284}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 1874, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 93717.8598104308}]
notes: []
