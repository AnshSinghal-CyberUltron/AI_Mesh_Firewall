# li-050: strict FAIL | load-knee PASS (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 15000 (50.0/s) qualified 14760 (49.2/s) FP-blocks 240 (0.016) expected-blocks 0 infra 0 (0.0) drops 0 safety 0 detection-misses 0
by class: {'benign': {'policy_block_fp': 240, 'qualified': 14760}}
infra reasons: {}
infra detail: {}

T_fw_addon: n=14760 p50=46.4749 p90=51.5202 p99=70.6199 p99.9=74.0858 max=92.7113 mean=36.6621
T_fw_addon_nohold: n=14760 p50=9.7514 p90=11.6908 p99=14.0871 p99.9=14.8693 max=18.7111 mean=9.078
T_fw_addon_sse: n=10331 p50=49.4238 p90=53.13 p99=70.9822 p99.9=74.9241 max=92.7113 mean=48.4648
T_fw_addon_json: n=4429 p50=9.8059 p90=11.7646 p99=14.1069 p99.9=14.8048 max=14.9951 mean=9.1314
T_addon_first_sse: n=10331 p50=9.6393 p90=11.9719 p99=27.5631 p99.9=32.7954 max=50.9704 mean=9.2891
T_addon_total_sse: n=10331 p50=9.7231 p90=11.6792 p99=14.0686 p99.9=14.9448 max=18.7111 mean=9.0551
T_addon_total_json: n=4429 p50=9.8059 p90=11.7646 p99=14.1069 p99.9=14.8048 max=14.9951 mean=9.1314
T_release_lag_max: n=10331 p50=49.4238 p90=53.13 p99=70.9822 p99.9=74.9241 max=92.7113 mean=48.4648
client_ttft_sse: n=10331 p50=159.6896 p90=162.034 p99=177.5835 p99.9=182.8316 max=201.0121 mean=159.3332
lateness: n=15000 p50=0.0885 p90=0.1039 p99=0.1181 p99.9=0.1368 max=0.271 mean=0.0852
loadgen: [{'vm': 'rv-pbu-lg-3', 'busy_max': 16.6, 'busy_mean': 4.6, 'late_max_us': 361, 'conn_opens': 149, 'max_inflight': 137}, {'vm': 'rv-pbu-lg-4', 'busy_max': 16.5, 'busy_mean': 4.6, 'late_max_us': 300, 'conn_opens': 149, 'max_inflight': 137}]
wire per client request: {'client_to_gw': 8335.2, 'gw_to_client': 56377.0, 'gw_to_provider': 9285.5, 'provider_to_gw': 57050.2}
gateway cores 1.713 cpu-ms/req 34.366 (workers 30.354, owners 4.004, redis 0.218)
worker util {'n': 18, 'min': 0.047, 'median': 0.087, 'max': 0.107, 'all_sorted': [0.047, 0.049, 0.052, 0.077, 0.079, 0.08, 0.082, 0.082, 0.087, 0.087, 0.09, 0.094, 0.097, 0.098, 0.098, 0.104, 0.105, 0.107]}; per-core schedstat max 0.097 mean 0.076; procstat max 0.137
gpu: {'0': {'samples': 298, 'sm_mean': 7.7, 'sm_max': 22.0, 'mem_mean': 1.3, 'fb_mb_max': 434.0}, '1': {'samples': 298, 'sm_mean': 7.8, 'sm_max': 21.0, 'mem_mean': 1.4, 'fb_mb_max': 434.0}}
rss max total by role (MB): {'redis': 1218.8, 'launcher': 59.7, 'owner': 3065.4, 'worker': 3682.2}; fds {'redis': 0, 'launcher': 7, 'owner': 128, 'worker': 979}
W t_input_ns: {'n': 14991, 'mean_ms': 7.7659, 'p50_ms': 8.4541, 'p90_ms': 10.1581, 'p99_ms': 12.6484, 'p99.9_ms': 13.0417, 'max_cum_ms': 47.8562}
W t_admit_ns: {'n': 14990, 'mean_ms': 0.0962, 'p50_ms': 0.0916, 'p90_ms': 0.1152, 'p99_ms': 0.1505, 'p99.9_ms': 0.7823, 'max_cum_ms': 3.1884}
W t_tokenize_ns: {'n': 14990, 'mean_ms': 3.1192, 'p50_ms': 3.1293, 'p90_ms': 4.5548, 'p99_ms': 5.1446, 'p99.9_ms': 5.7344, 'max_cum_ms': 43.5723}
W t_det_scan_ns: {'n': 14990, 'mean_ms': 0.0981, 'p50_ms': 0.0988, 'p90_ms': 0.1193, 'p99_ms': 0.1444, 'p99.9_ms': 0.1833, 'max_cum_ms': 1.1228}
W t_guard_wait_ns: {'n': 14991, 'mean_ms': 4.0773, 'p50_ms': 4.6858, 'p90_ms': 5.0135, 'p99_ms': 7.0451, 'p99.9_ms': 7.1762, 'max_cum_ms': 43.2734}
W guard_owner_rtt_ns: {'n': 14991, 'mean_ms': 4.2653, 'p50_ms': 4.948, 'p90_ms': 5.2101, 'p99_ms': 7.2417, 'p99.9_ms': 7.4383, 'max_cum_ms': 40.4487}
W guard_queue_ns: {'n': 14991, 'mean_ms': 0.1052, 'p50_ms': 0.1039, 'p90_ms': 0.1213, 'p99_ms': 0.1444, 'p99.9_ms': 0.1649, 'max_cum_ms': 12.7038}
W guard_exec_ns: {'n': 14991, 'mean_ms': 3.5642, 'p50_ms': 4.2271, 'p90_ms': 4.4237, 'p99_ms': 6.4553, 'p99.9_ms': 6.5864, 'max_cum_ms': 9.4148}
W dispatch_headers_ns: {'n': 14755, 'mean_ms': 1519.6973, 'p50_ms': 149.9464, 'p90_ms': 5872.0256, 'p99_ms': 7885.2915, 'p99.9_ms': 8153.727, 'max_cum_ms': 8134.3514}
W release_lag_ns: {'n': 2290922, 'mean_ms': 19.8919, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 100.1277}
W holdback_wait_ns: {'n': 2232793, 'mean_ms': 20.3424, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 100.0541}
W release_processing_ns: {'n': 2290922, 'mean_ms': 0.0656, 'p50_ms': 0.0643, 'p90_ms': 0.0865, 'p99_ms': 0.1172, 'p99.9_ms': 0.1567, 'max_cum_ms': 33.8621}
W t_finalize_ns: {'n': 14772, 'mean_ms': 0.2708, 'p50_ms': 0.2642, 'p90_ms': 0.3379, 'p99_ms': 0.4035, 'p99.9_ms': 0.4567, 'max_cum_ms': 3.3822}
W loop_lag_ns: {'n': 53969, 'mean_ms': 0.1003, 'p50_ms': 0.0, 'p90_ms': 0.3133, 'p99_ms': 1.0732, 'p99.9_ms': 1.6794, 'max_cum_ms': 37.9964}
W guard_windows_per_request: {'n': 14990, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_exec_ns: {'n': 14991, 'mean_ms': 3.5642, 'p50_ms': 4.2271, 'p90_ms': 4.4237, 'p99_ms': 6.4553, 'p99.9_ms': 6.5864, 'max_cum_ms': 9.4148}
O guard_batch_windows: {'n': 14991, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_queue_ns: {'n': 14991, 'mean_ms': 0.1052, 'p50_ms': 0.1039, 'p90_ms': 0.1213, 'p99_ms': 0.1444, 'p99.9_ms': 0.1649, 'max_cum_ms': 12.7038}
worker counts: {'admitted': 14990, 'audit_enqueued': 29763, 'audit_written': 29763, 'background_round_trips': 10785, 'disposition_ALLOW': 14753, 'disposition_BLOCK': 238, 'guard_windows': 24748, 'lease_granted_tokens{org="org-a"}': 14586240, 'lease_refills': 71, 'provider_calls': 14753, 'provider_connections_opened': 2905, 'quota_admitted_tokens{org="org-a"}': 14226210, 'requests_by_round_trips{n="0"}': 14919, 'requests_by_round_trips{n="1"}': 71, 'shared_state_round_trips': 71}
owner counts: {'guard_batches': 14991, 'guard_windows': 24748, 'owner_requests': 14990, 'owner_windows': 24746}
gauges: {'audit_queue_bound': [181], 'guard_tokens_per_s': [25859.570924769152, 26277.94409965762], 'audit_completeness_ratio': [1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'input_queue_cap': [68.0, 69.0], 'guard_queue_cap_tokens': [25859.0, 26277.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 232736, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 232736.13832292237}, {'owner_clients': 9, 'owner_queued_tokens': 512, 'owner_queue_cap_tokens': 236501, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 236501.49689691857}]
notes: []
