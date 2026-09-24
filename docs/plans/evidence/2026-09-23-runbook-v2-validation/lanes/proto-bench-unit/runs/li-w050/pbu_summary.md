# li-w050: strict FAIL | load-knee PASS (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 15000 (50.0/s) qualified 14607 (48.69/s) FP-blocks 393 (0.0262) expected-blocks 0 infra 0 (0.0) drops 0 safety 0 detection-misses 0
by class: {'benign': {'policy_block_fp': 393, 'qualified': 14607}}
infra reasons: {}
infra detail: {}

T_fw_addon: n=14607 p50=55.0004 p90=56.7035 p99=75.9206 p99.9=93.8384 max=96.0479 mean=44.7776
T_fw_addon_nohold: n=14607 p50=15.3539 p90=16.308 p99=16.9487 p99.9=17.3165 max=25.909 mean=15.3308
T_fw_addon_sse: n=10228 p50=55.5545 p90=65.8435 p99=76.0891 p99.9=95.0108 max=96.0479 mean=57.3311
T_fw_addon_json: n=4379 p50=15.4757 p90=16.3859 p99=16.9928 p99.9=17.318 max=25.1787 mean=15.4564
T_addon_first_sse: n=10228 p50=15.2165 p90=16.3025 p99=34.8095 p99.9=36.3468 max=55.8393 mean=15.5237
T_addon_total_sse: n=10228 p50=15.2992 p90=16.2683 p99=16.9266 p99.9=17.2659 max=25.909 mean=15.277
T_addon_total_json: n=4379 p50=15.4757 p90=16.3859 p99=16.9928 p99.9=17.318 max=25.1787 mean=15.4564
T_release_lag_max: n=10228 p50=55.5545 p90=65.8435 p99=76.0891 p99.9=95.0108 max=96.0479 mean=57.3311
client_ttft_sse: n=10228 p50=165.2679 p90=166.3483 p99=184.8311 p99.9=186.364 max=205.8406 mean=165.5651
lateness: n=15000 p50=0.0835 p90=0.0963 p99=0.1084 p99.9=0.149 max=0.3088 mean=0.0835
loadgen: [{'vm': 'rv-pbu-lg-3', 'busy_max': 16.5, 'busy_mean': 5.7, 'late_max_us': 437, 'conn_opens': 204, 'max_inflight': 203}, {'vm': 'rv-pbu-lg-4', 'busy_max': 16.2, 'busy_mean': 5.6, 'late_max_us': 308, 'conn_opens': 204, 'max_inflight': 203}]
wire per client request: {'client_to_gw': 13840.4, 'gw_to_client': 98117.9, 'gw_to_provider': 13234.7, 'provider_to_gw': 99478.6}
gateway cores 2.517 cpu-ms/req 50.499 (workers 43.624, owners 6.867, redis 0.22)
worker util {'n': 18, 'min': 0.083, 'median': 0.123, 'max': 0.162, 'all_sorted': [0.083, 0.091, 0.091, 0.095, 0.098, 0.106, 0.106, 0.117, 0.122, 0.123, 0.129, 0.131, 0.134, 0.136, 0.148, 0.148, 0.154, 0.162]}; per-core schedstat max 0.14 mean 0.109; procstat max 0.142
gpu: {'0': {'samples': 298, 'sm_mean': 14.1, 'sm_max': 29.0, 'mem_mean': 2.5, 'fb_mb_max': 434.0}, '1': {'samples': 298, 'sm_mean': 13.7, 'sm_max': 29.0, 'mem_mean': 2.5, 'fb_mb_max': 434.0}}
rss max total by role (MB): {'redis': 2696.0, 'launcher': 59.7, 'owner': 3065.6, 'worker': 3698.3}; fds {'redis': 0, 'launcher': 7, 'owner': 126, 'worker': 1250}
W t_input_ns: {'n': 14991, 'mean_ms': 13.347, 'p50_ms': 13.3038, 'p90_ms': 13.8281, 'p99_ms': 14.4835, 'p99.9_ms': 15.6631, 'max_cum_ms': 53.3673}
W t_admit_ns: {'n': 14990, 'mean_ms': 0.0939, 'p50_ms': 0.0886, 'p90_ms': 0.0947, 'p99_ms': 0.2099, 'p99.9_ms': 0.514, 'max_cum_ms': 3.5161}
W t_tokenize_ns: {'n': 14991, 'mean_ms': 5.7699, 'p50_ms': 5.7999, 'p90_ms': 6.2587, 'p99_ms': 6.8485, 'p99.9_ms': 8.0937, 'max_cum_ms': 45.5791}
W t_det_scan_ns: {'n': 14991, 'mean_ms': 0.1397, 'p50_ms': 0.1321, 'p90_ms': 0.1731, 'p99_ms': 0.2161, 'p99.9_ms': 0.2365, 'max_cum_ms': 1.1228}
W t_guard_wait_ns: {'n': 14991, 'mean_ms': 6.945, 'p50_ms': 6.914, 'p90_ms': 7.1107, 'p99_ms': 7.2417, 'p99.9_ms': 7.6349, 'max_cum_ms': 46.2028}
W guard_owner_rtt_ns: {'n': 14991, 'mean_ms': 7.2352, 'p50_ms': 7.2417, 'p90_ms': 7.3728, 'p99_ms': 7.5694, 'p99.9_ms': 7.9626, 'max_cum_ms': 40.4487}
W guard_queue_ns: {'n': 14991, 'mean_ms': 0.118, 'p50_ms': 0.1162, 'p90_ms': 0.1362, 'p99_ms': 0.1649, 'p99.9_ms': 0.1956, 'max_cum_ms': 19.3312}
W guard_exec_ns: {'n': 14991, 'mean_ms': 6.3798, 'p50_ms': 6.3898, 'p90_ms': 6.4553, 'p99_ms': 6.5864, 'p99.9_ms': 6.8485, 'max_cum_ms': 9.4148}
W dispatch_headers_ns: {'n': 14610, 'mean_ms': 2545.2611, 'p50_ms': 152.0435, 'p90_ms': 8153.727, 'p99_ms': 8153.727, 'p99.9_ms': 8153.727, 'max_cum_ms': 8142.8253}
W release_lag_ns: {'n': 4032589, 'mean_ms': 19.9594, 'p50_ms': 20.054, 'p90_ms': 20.3162, 'p99_ms': 40.108, 'p99.9_ms': 40.6323, 'max_cum_ms': 100.3546}
W holdback_wait_ns: {'n': 3945142, 'mean_ms': 20.3402, 'p50_ms': 20.054, 'p90_ms': 20.3162, 'p99_ms': 40.108, 'p99.9_ms': 40.6323, 'max_cum_ms': 100.2734}
W release_processing_ns: {'n': 4032589, 'mean_ms': 0.0603, 'p50_ms': 0.0561, 'p90_ms': 0.0906, 'p99_ms': 0.1275, 'p99.9_ms': 0.169, 'max_cum_ms': 40.1171}
W t_finalize_ns: {'n': 14607, 'mean_ms': 0.3118, 'p50_ms': 0.3133, 'p90_ms': 0.3953, 'p99_ms': 0.4649, 'p99.9_ms': 0.5366, 'max_cum_ms': 4.4128}
W loop_lag_ns: {'n': 53916, 'mean_ms': 0.2066, 'p50_ms': 0.0242, 'p90_ms': 0.8069, 'p99_ms': 1.45, 'p99.9_ms': 2.1135, 'max_cum_ms': 52.8097}
W guard_windows_per_request: {'n': 14991, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_exec_ns: {'n': 14991, 'mean_ms': 6.3798, 'p50_ms': 6.3898, 'p90_ms': 6.4553, 'p99_ms': 6.5864, 'p99.9_ms': 6.8485, 'max_cum_ms': 9.4148}
O guard_batch_windows: {'n': 14991, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_queue_ns: {'n': 14991, 'mean_ms': 0.118, 'p50_ms': 0.1162, 'p90_ms': 0.1362, 'p99_ms': 0.1649, 'p99.9_ms': 0.1956, 'max_cum_ms': 19.3312}
worker counts: {'admitted': 14990, 'audit_enqueued': 29598, 'audit_written': 29598, 'background_round_trips': 10780, 'disposition_ALLOW': 14598, 'disposition_BLOCK': 393, 'guard_windows': 44973, 'lease_granted_tokens{org="org-a"}': 24858240, 'lease_refills': 121, 'provider_calls': 14598, 'provider_connections_opened': 922, 'quota_admitted_tokens{org="org-a"}': 25162625, 'requests_by_round_trips{n="0"}': 14869, 'requests_by_round_trips{n="1"}': 121, 'shared_state_round_trips': 121}
owner counts: {'guard_batches': 14991, 'guard_windows': 44973, 'owner_requests': 14991, 'owner_windows': 44973}
gauges: {'audit_queue_bound': [181], 'guard_tokens_per_s': [25859.570924769152, 26277.94409965762], 'audit_completeness_ratio': [1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'input_queue_cap': [68.0, 69.0], 'guard_queue_cap_tokens': [25859.0, 26277.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 232736, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 232736.13832292237}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 236501, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 236501.49689691857}]
notes: []
