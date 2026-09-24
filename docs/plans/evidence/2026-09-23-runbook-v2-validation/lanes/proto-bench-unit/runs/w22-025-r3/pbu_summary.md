# w22-025-r3: strict FAIL | load-knee PASS (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 7500 (25.0/s) qualified 7333 (24.44/s) FP-blocks 167 (0.02227) expected-blocks 0 infra 0 (0.0) drops 0 safety 0 detection-misses 0
by class: {'benign': {'policy_block_fp': 167, 'qualified': 7333}}
infra reasons: {}
infra detail: {}

T_fw_addon: n=7333 p50=14.468 p90=17.2203 p99=56.9144 p99.9=74.5446 max=97.3509 mean=17.6549
T_fw_addon_nohold: n=7333 p50=14.3303 p90=14.9295 p99=19.041 p99.9=21.7998 max=22.5239 mean=14.3816
T_fw_addon_sse: n=5132 p50=14.4582 p90=34.5735 p99=61.9868 p99.9=74.6985 max=97.3509 mean=18.9937
T_fw_addon_json: n=2201 p50=14.4798 p90=15.0334 p99=19.9785 p99.9=21.7361 max=22.5239 mean=14.5332
T_addon_first_sse: n=5132 p50=14.1899 p90=14.9599 p99=33.9807 p99.9=34.7934 max=73.8781 mean=14.6541
T_addon_total_sse: n=5132 p50=14.2659 p90=14.8714 p99=18.4105 p99.9=21.7998 max=22.1562 mean=14.3166
T_addon_total_json: n=2201 p50=14.4798 p90=15.0334 p99=19.9785 p99.9=21.7361 max=22.5239 mean=14.5332
T_release_lag_max: n=504 p50=54.4045 p90=61.9868 p99=74.6985 p99.9=97.3509 max=97.3509 mean=56.6575
client_ttft_sse: n=5132 p50=164.2352 p90=165.0083 p99=184.0068 p99.9=184.8238 max=223.8891 mean=164.7011
lateness: n=7500 p50=0.0884 p90=0.1002 p99=0.1151 p99.9=0.1475 max=0.3379 mean=0.0895
loadgen: [{'vm': 'rv-pbu-lg-1', 'busy_max': 18.1, 'busy_mean': 4.7, 'late_max_us': 256, 'conn_opens': 103, 'max_inflight': 102}, {'vm': 'rv-pbu-lg-2', 'busy_max': 18.1, 'busy_mean': 4.8, 'late_max_us': 337, 'conn_opens': 103, 'max_inflight': 102}]
wire per client request: {'client_to_gw': 13823.9, 'gw_to_client': 98745.4, 'gw_to_provider': 13721.5, 'provider_to_gw': 99694.6}
gateway cores 1.468 cpu-ms/req 58.935 (workers 51.871, owners 7.046, redis 0.292)
worker util {'n': 18, 'min': 0.052, 'median': 0.072, 'max': 0.11}; per-core schedstat max 0.079 mean 0.064; procstat max 0.08
gpu: {'0': {'samples': 299, 'sm_mean': 7.3, 'sm_max': 17.0, 'mem_mean': 1.3, 'fb_mb_max': 434.0}, '1': {'samples': 299, 'sm_mean': 6.3, 'sm_max': 17.0, 'mem_mean': 1.1, 'fb_mb_max': 434.0}}
rss max total by role (MB): {'redis': 2827.8, 'launcher': 59.7, 'owner': 3067.2, 'worker': 3699.8}; fds {'redis': 0, 'launcher': 7, 'owner': 131, 'worker': 859}
W t_input_ns: {'n': 7489, 'mean_ms': 12.8041, 'p50_ms': 12.7795, 'p90_ms': 13.1727, 'p99_ms': 14.8767, 'p99.9_ms': 20.054, 'max_cum_ms': 32.2244}
W t_admit_ns: {'n': 7489, 'mean_ms': 0.0932, 'p50_ms': 0.0886, 'p90_ms': 0.0937, 'p99_ms': 0.2099, 'p99.9_ms': 0.51, 'max_cum_ms': 3.9185}
W t_tokenize_ns: {'n': 7489, 'mean_ms': 5.1319, 'p50_ms': 5.2101, 'p90_ms': 5.4723, 'p99_ms': 5.8655, 'p99.9_ms': 7.5039, 'max_cum_ms': 8.7407}
W t_det_scan_ns: {'n': 7489, 'mean_ms': 0.1243, 'p50_ms': 0.1213, 'p90_ms': 0.1341, 'p99_ms': 0.1812, 'p99.9_ms': 0.1997, 'max_cum_ms': 24.0812}
W t_guard_wait_ns: {'n': 7489, 'mean_ms': 7.0837, 'p50_ms': 7.0451, 'p90_ms': 7.1762, 'p99_ms': 7.9626, 'p99.9_ms': 14.0902, 'max_cum_ms': 29.4642}
W guard_owner_rtt_ns: {'n': 7489, 'mean_ms': 7.3152, 'p50_ms': 7.3073, 'p90_ms': 7.4383, 'p99_ms': 8.0937, 'p99.9_ms': 12.9106, 'max_cum_ms': 28.5289}
W guard_queue_ns: {'n': 7489, 'mean_ms': 0.1226, 'p50_ms': 0.1193, 'p90_ms': 0.1382, 'p99_ms': 0.1628, 'p99.9_ms': 0.2345, 'max_cum_ms': 12.2232}
W guard_exec_ns: {'n': 7489, 'mean_ms': 6.4505, 'p50_ms': 6.4553, 'p90_ms': 6.5208, 'p99_ms': 6.783, 'p99.9_ms': 7.1107, 'max_cum_ms': 8.4399}
W dispatch_headers_ns: {'n': 7325, 'mean_ms': 2549.9015, 'p50_ms': 149.9464, 'p90_ms': 8153.727, 'p99_ms': 8153.727, 'p99.9_ms': 8153.727, 'max_cum_ms': 8151.0442}
W release_lag_ns: {'n': 2021937, 'mean_ms': 19.9591, 'p50_ms': 20.054, 'p90_ms': 20.3162, 'p99_ms': 40.108, 'p99.9_ms': 40.6323, 'max_cum_ms': 150.0061}
W holdback_wait_ns: {'n': 1977926, 'mean_ms': 20.3397, 'p50_ms': 20.054, 'p90_ms': 20.3162, 'p99_ms': 40.108, 'p99.9_ms': 40.6323, 'max_cum_ms': 149.9429}
W release_processing_ns: {'n': 2021937, 'mean_ms': 0.0621, 'p50_ms': 0.0596, 'p90_ms': 0.0896, 'p99_ms': 0.1254, 'p99.9_ms': 0.1628, 'max_cum_ms': 26.6219}
W t_finalize_ns: {'n': 7329, 'mean_ms': 0.3033, 'p50_ms': 0.3011, 'p90_ms': 0.383, 'p99_ms': 0.4526, 'p99.9_ms': 0.51, 'max_cum_ms': 2.7484}
W loop_lag_ns: {'n': 53570, 'mean_ms': 0.7835, 'p50_ms': 0.106, 'p90_ms': 1.3189, 'p99_ms': 7.5039, 'p99.9_ms': 9.6338, 'max_cum_ms': 56.9536}
W guard_windows_per_request: {'n': 7489, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_exec_ns: {'n': 7494, 'mean_ms': 6.4505, 'p50_ms': 6.4553, 'p90_ms': 6.5208, 'p99_ms': 6.783, 'p99.9_ms': 7.1107, 'max_cum_ms': 8.4399}
O guard_batch_windows: {'n': 7494, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_queue_ns: {'n': 7494, 'mean_ms': 0.1226, 'p50_ms': 0.1193, 'p90_ms': 0.1382, 'p99_ms': 0.1628, 'p99.9_ms': 0.2345, 'max_cum_ms': 12.2232}
worker counts: {'admitted': 7489, 'audit_enqueued': 14818, 'audit_written': 14818, 'background_round_trips': 10714, 'disposition_ALLOW': 7323, 'disposition_BLOCK': 166, 'guard_windows': 22467, 'lease_granted_tokens{org="org-a"}': 12737280, 'lease_refills': 62, 'provider_calls': 7323, 'provider_connections_opened': 770, 'quota_admitted_tokens{org="org-a"}': 12572584, 'requests_by_round_trips{n="0"}': 7427, 'requests_by_round_trips{n="1"}': 62, 'shared_state_round_trips': 62}
owner counts: {'guard_batches': 7494, 'guard_windows': 22482, 'owner_requests': 7494, 'owner_windows': 22482}
gauges: {'audit_queue_bound': [181], 'audit_completeness_ratio': [1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'guard_tokens_per_s': [25768.91943081631, 26183.826731337853], 'input_queue_cap': [2.0], 'guard_queue_cap_tokens': [515.0, 523.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4638, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 231920.2748773468}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4713, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 235654.44058204067}]
notes: []
