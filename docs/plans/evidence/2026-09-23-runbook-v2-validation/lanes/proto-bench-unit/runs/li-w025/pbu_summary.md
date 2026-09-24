# li-w025: strict FAIL | load-knee PASS (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 7500 (25.0/s) qualified 7328 (24.43/s) FP-blocks 172 (0.02293) expected-blocks 0 infra 0 (0.0) drops 0 safety 0 detection-misses 0
by class: {'benign': {'policy_block_fp': 172, 'qualified': 7328}}
infra reasons: {}
infra detail: {}

T_fw_addon: n=7328 p50=54.1441 p90=55.1601 p99=74.6736 p99.9=75.3849 max=94.5037 mean=43.922
T_fw_addon_nohold: n=7328 p50=14.5812 p90=15.1085 p99=15.5236 p99.9=16.8741 max=22.4241 mean=14.5364
T_fw_addon_sse: n=5129 p50=54.4991 p90=73.2662 p99=74.7521 p99.9=76.6858 max=94.5037 mean=56.4599
T_fw_addon_json: n=2199 p50=14.7434 p90=15.2335 p99=15.6238 p99.9=16.7366 max=17.2524 mean=14.6785
T_addon_first_sse: n=5129 p50=14.3693 p90=14.9232 p99=33.5304 p99.9=34.8311 max=53.8967 mean=14.5689
T_addon_total_sse: n=5129 p50=14.5209 p90=15.0368 p99=15.4472 p99.9=16.895 max=22.4241 mean=14.4755
T_addon_total_json: n=2199 p50=14.7434 p90=15.2335 p99=15.6238 p99.9=16.7366 max=17.2524 mean=14.6785
T_release_lag_max: n=5129 p50=54.4991 p90=73.2662 p99=74.7521 p99.9=76.6858 max=94.5037 mean=56.4599
client_ttft_sse: n=5129 p50=164.4129 p90=164.9683 p99=183.642 p99.9=184.871 max=203.9061 mean=164.6139
lateness: n=7500 p50=0.0861 p90=0.0984 p99=0.1157 p99.9=0.1812 max=0.2905 mean=0.0865
loadgen: [{'vm': 'rv-pbu-lg-3', 'busy_max': 15.7, 'busy_mean': 4.9, 'late_max_us': 331, 'conn_opens': 103, 'max_inflight': 102}, {'vm': 'rv-pbu-lg-4', 'busy_max': 18.4, 'busy_mean': 4.8, 'late_max_us': 306, 'conn_opens': 103, 'max_inflight': 102}]
wire per client request: {'client_to_gw': 13562.9, 'gw_to_client': 98326.4, 'gw_to_provider': 14136.0, 'provider_to_gw': 99658.2}
gateway cores 1.42 cpu-ms/req 56.984 (workers 49.999, owners 6.967, redis 0.304)
worker util {'n': 18, 'min': 0.044, 'median': 0.073, 'max': 0.09, 'all_sorted': [0.044, 0.052, 0.055, 0.061, 0.063, 0.064, 0.065, 0.071, 0.072, 0.073, 0.074, 0.074, 0.075, 0.075, 0.077, 0.08, 0.082, 0.09]}; per-core schedstat max 0.118 mean 0.062; procstat max 0.119
gpu: {'0': {'samples': 298, 'sm_mean': 6.7, 'sm_max': 17.0, 'mem_mean': 1.2, 'fb_mb_max': 434.0}, '1': {'samples': 298, 'sm_mean': 6.5, 'sm_max': 17.0, 'mem_mean': 1.2, 'fb_mb_max': 434.0}}
rss max total by role (MB): {'redis': 2594.7, 'launcher': 59.7, 'owner': 3065.5, 'worker': 3697.1}; fds {'redis': 0, 'launcher': 7, 'owner': 131, 'worker': 846}
W t_input_ns: {'n': 7495, 'mean_ms': 12.9315, 'p50_ms': 12.9106, 'p90_ms': 13.3038, 'p99_ms': 13.697, 'p99.9_ms': 15.532, 'max_cum_ms': 53.3673}
W t_admit_ns: {'n': 7495, 'mean_ms': 0.0959, 'p50_ms': 0.0896, 'p90_ms': 0.0998, 'p99_ms': 0.214, 'p99.9_ms': 0.5284, 'max_cum_ms': 3.5161}
W t_tokenize_ns: {'n': 7495, 'mean_ms': 5.3166, 'p50_ms': 5.4067, 'p90_ms': 5.6689, 'p99_ms': 5.931, 'p99.9_ms': 7.8971, 'max_cum_ms': 45.5791}
W t_det_scan_ns: {'n': 7495, 'mean_ms': 0.1249, 'p50_ms': 0.1203, 'p90_ms': 0.1403, 'p99_ms': 0.1731, 'p99.9_ms': 0.1997, 'max_cum_ms': 1.1228}
W t_guard_wait_ns: {'n': 7495, 'mean_ms': 7.0152, 'p50_ms': 7.0451, 'p90_ms': 7.1107, 'p99_ms': 7.2417, 'p99.9_ms': 7.5039, 'max_cum_ms': 46.2028}
W guard_owner_rtt_ns: {'n': 7495, 'mean_ms': 7.2752, 'p50_ms': 7.3073, 'p90_ms': 7.3728, 'p99_ms': 7.5039, 'p99.9_ms': 7.8316, 'max_cum_ms': 40.4487}
W guard_queue_ns: {'n': 7495, 'mean_ms': 0.1212, 'p50_ms': 0.1203, 'p90_ms': 0.1362, 'p99_ms': 0.1546, 'p99.9_ms': 0.171, 'max_cum_ms': 19.3312}
W guard_exec_ns: {'n': 7495, 'mean_ms': 6.4445, 'p50_ms': 6.4553, 'p90_ms': 6.5208, 'p99_ms': 6.6519, 'p99.9_ms': 6.914, 'max_cum_ms': 9.4148}
W dispatch_headers_ns: {'n': 7330, 'mean_ms': 2547.1367, 'p50_ms': 152.0435, 'p90_ms': 8153.727, 'p99_ms': 8153.727, 'p99.9_ms': 8153.727, 'max_cum_ms': 8134.3514}
W release_lag_ns: {'n': 2022211, 'mean_ms': 19.966, 'p50_ms': 20.054, 'p90_ms': 20.3162, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 100.3546}
W holdback_wait_ns: {'n': 1978391, 'mean_ms': 20.3404, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 100.2734}
W release_processing_ns: {'n': 2022211, 'mean_ms': 0.0664, 'p50_ms': 0.0643, 'p90_ms': 0.0927, 'p99_ms': 0.1265, 'p99.9_ms': 0.1628, 'max_cum_ms': 40.1171}
W t_finalize_ns: {'n': 7329, 'mean_ms': 0.3136, 'p50_ms': 0.3133, 'p90_ms': 0.3912, 'p99_ms': 0.4444, 'p99.9_ms': 0.4936, 'max_cum_ms': 4.4128}
W loop_lag_ns: {'n': 53951, 'mean_ms': 0.1489, 'p50_ms': 0.0068, 'p90_ms': 0.5693, 'p99_ms': 1.1551, 'p99.9_ms': 1.9907, 'max_cum_ms': 52.8097}
W guard_windows_per_request: {'n': 7495, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_exec_ns: {'n': 7495, 'mean_ms': 6.4445, 'p50_ms': 6.4553, 'p90_ms': 6.5208, 'p99_ms': 6.6519, 'p99.9_ms': 6.914, 'max_cum_ms': 9.4148}
O guard_batch_windows: {'n': 7495, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_queue_ns: {'n': 7495, 'mean_ms': 0.1212, 'p50_ms': 0.1203, 'p90_ms': 0.1362, 'p99_ms': 0.1546, 'p99.9_ms': 0.171, 'max_cum_ms': 19.3312}
worker counts: {'admitted': 7495, 'audit_enqueued': 14824, 'audit_written': 14824, 'background_round_trips': 10784, 'disposition_ALLOW': 7324, 'disposition_BLOCK': 171, 'guard_windows': 22485, 'lease_granted_tokens{org="org-a"}': 12737280, 'lease_refills': 62, 'provider_calls': 7324, 'provider_connections_opened': 1612, 'quota_admitted_tokens{org="org-a"}': 12579998, 'requests_by_round_trips{n="0"}': 7433, 'requests_by_round_trips{n="1"}': 62, 'shared_state_round_trips': 62}
owner counts: {'guard_batches': 7495, 'guard_windows': 22485, 'owner_requests': 7495, 'owner_windows': 22485}
gauges: {'audit_queue_bound': [181], 'guard_tokens_per_s': [25859.570924769152, 26277.94409965762], 'audit_completeness_ratio': [1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'input_queue_cap': [68.0, 69.0], 'guard_queue_cap_tokens': [25859.0, 26277.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 232736, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 232736.13832292237}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 236501, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 236501.49689691857}]
notes: []
