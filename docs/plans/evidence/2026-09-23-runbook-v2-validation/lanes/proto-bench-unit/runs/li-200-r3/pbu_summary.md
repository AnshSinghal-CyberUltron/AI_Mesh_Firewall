# li-200-r3: strict FAIL | load-knee PASS (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 60000 (200.0/s) qualified 58930 (196.43/s) FP-blocks 1070 (0.01783) expected-blocks 0 infra 0 (0.0) drops 0 safety 0 detection-misses 0
by class: {'benign': {'policy_block_fp': 1070, 'qualified': 58930}}
infra reasons: {}
infra detail: {}

T_fw_addon: n=58930 p50=47.6588 p90=54.1146 p99=72.437 p99.9=78.1613 max=117.7137 mean=38.2681
T_fw_addon_nohold: n=58930 p50=10.8122 p90=14.2851 p99=18.069 p99.9=20.1729 max=35.4134 mean=10.6002
T_fw_addon_sse: n=41259 p50=50.4819 p90=55.5728 p99=73.1789 p99.9=85.5575 max=117.7137 mean=50.0777
T_fw_addon_json: n=17671 p50=10.8794 p90=14.4585 p99=18.1672 p99.9=20.345 max=25.3199 mean=10.6943
T_addon_first_sse: n=41259 p50=10.7172 p90=14.429 p99=28.8681 p99.9=35.5439 max=54.3602 mean=10.766
T_addon_total_sse: n=41259 p50=10.7837 p90=14.216 p99=17.9991 p99.9=20.1074 max=35.4134 mean=10.5599
T_addon_total_json: n=17671 p50=10.8794 p90=14.4585 p99=18.1672 p99.9=20.345 max=25.3199 mean=10.6943
T_release_lag_max: n=41259 p50=50.4819 p90=55.5728 p99=73.1789 p99.9=85.5575 max=117.7137 mean=50.0777
client_ttft_sse: n=41259 p50=160.7546 p90=164.4812 p99=178.9311 p99.9=185.5447 max=204.4331 mean=160.8066
lateness: n=60000 p50=0.0836 p90=0.0932 p99=0.1039 p99.9=0.1306 max=0.2698 mean=0.0838
loadgen: [{'vm': 'rv-pbu-lg-3', 'busy_max': 11.3, 'busy_mean': 7.8, 'late_max_us': 869, 'conn_opens': 517, 'max_inflight': 497}, {'vm': 'rv-pbu-lg-4', 'busy_max': 8.1, 'busy_mean': 7.6, 'late_max_us': 279, 'conn_opens': 521, 'max_inflight': 497}]
wire per client request: {'client_to_gw': 7862.6, 'gw_to_client': 56385.4, 'gw_to_provider': 8590.0, 'provider_to_gw': 57066.6}
gateway cores 7.096 cpu-ms/req 35.6 (workers 31.554, owners 4.044, redis 0.167)
worker util {'n': 18, 'min': 0.267, 'median': 0.359, 'max': 0.397, 'all_sorted': [0.267, 0.294, 0.297, 0.299, 0.307, 0.324, 0.338, 0.352, 0.359, 0.359, 0.377, 0.38, 0.381, 0.384, 0.389, 0.391, 0.395, 0.397]}; per-core schedstat max 0.312 mean 0.302; procstat max 0.426
gpu: {'0': {'samples': 298, 'sm_mean': 29.1, 'sm_max': 49.0, 'mem_mean': 5.3, 'fb_mb_max': 434.0}, '1': {'samples': 298, 'sm_mean': 31.3, 'sm_max': 56.0, 'mem_mean': 5.7, 'fb_mb_max': 434.0}}
rss max total by role (MB): {'redis': 2032.6, 'launcher': 59.7, 'owner': 3065.5, 'worker': 3688.8}; fds {'redis': 0, 'launcher': 7, 'owner': 126, 'worker': 2422}
W t_input_ns: {'n': 59967, 'mean_ms': 9.0621, 'p50_ms': 9.5027, 'p90_ms': 12.6484, 'p99_ms': 16.1874, 'p99.9_ms': 18.219, 'max_cum_ms': 48.3649}
W t_admit_ns: {'n': 59964, 'mean_ms': 0.0945, 'p50_ms': 0.0896, 'p90_ms': 0.1121, 'p99_ms': 0.1485, 'p99.9_ms': 0.7905, 'max_cum_ms': 3.1884}
W t_tokenize_ns: {'n': 59965, 'mean_ms': 3.8336, 'p50_ms': 3.7847, 'p90_ms': 5.7999, 'p99_ms': 6.914, 'p99.9_ms': 7.7005, 'max_cum_ms': 44.3103}
W t_det_scan_ns: {'n': 59965, 'mean_ms': 0.1189, 'p50_ms': 0.1121, 'p90_ms': 0.169, 'p99_ms': 0.2161, 'p99.9_ms': 0.2509, 'max_cum_ms': 1.1228}
W t_guard_wait_ns: {'n': 59967, 'mean_ms': 4.5889, 'p50_ms': 4.948, 'p90_ms': 6.9796, 'p99_ms': 10.1581, 'p99.9_ms': 12.2552, 'max_cum_ms': 43.2734}
W guard_owner_rtt_ns: {'n': 59967, 'mean_ms': 4.792, 'p50_ms': 5.1446, 'p90_ms': 7.2417, 'p99_ms': 10.4202, 'p99.9_ms': 12.5174, 'max_cum_ms': 40.4487}
W guard_queue_ns: {'n': 59967, 'mean_ms': 0.4362, 'p50_ms': 0.1142, 'p90_ms': 1.237, 'p99_ms': 5.2101, 'p99.9_ms': 8.1592, 'max_cum_ms': 16.4869}
W guard_exec_ns: {'n': 59967, 'mean_ms': 3.6054, 'p50_ms': 4.2926, 'p90_ms': 4.5548, 'p99_ms': 6.7174, 'p99.9_ms': 6.8485, 'max_cum_ms': 9.4148}
W dispatch_headers_ns: {'n': 58950, 'mean_ms': 1506.3384, 'p50_ms': 149.9464, 'p90_ms': 5872.0256, 'p99_ms': 7952.4004, 'p99.9_ms': 8153.727, 'max_cum_ms': 8134.3514}
W release_lag_ns: {'n': 9200894, 'mean_ms': 19.8918, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 100.1277}
W holdback_wait_ns: {'n': 8967086, 'mean_ms': 20.3402, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 100.0541}
W release_processing_ns: {'n': 9200894, 'mean_ms': 0.0685, 'p50_ms': 0.0671, 'p90_ms': 0.0916, 'p99_ms': 0.1224, 'p99.9_ms': 0.1649, 'max_cum_ms': 35.4319}
W t_finalize_ns: {'n': 58942, 'mean_ms': 0.2846, 'p50_ms': 0.2806, 'p90_ms': 0.3584, 'p99_ms': 0.4157, 'p99.9_ms': 0.4608, 'max_cum_ms': 3.3847}
W loop_lag_ns: {'n': 53988, 'mean_ms': 0.0716, 'p50_ms': 0.0, 'p90_ms': 0.1976, 'p99_ms': 0.9708, 'p99.9_ms': 1.5483, 'max_cum_ms': 37.9964}
W guard_windows_per_request: {'n': 59965, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_exec_ns: {'n': 59967, 'mean_ms': 3.6054, 'p50_ms': 4.2926, 'p90_ms': 4.5548, 'p99_ms': 6.7174, 'p99.9_ms': 6.8485, 'max_cum_ms': 9.4148}
O guard_batch_windows: {'n': 59967, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_queue_ns: {'n': 59967, 'mean_ms': 0.4362, 'p50_ms': 0.1142, 'p90_ms': 1.237, 'p99_ms': 5.2101, 'p99.9_ms': 8.1592, 'max_cum_ms': 16.4869}
worker counts: {'admitted': 59964, 'audit_enqueued': 118909, 'audit_written': 118909, 'background_round_trips': 10785, 'disposition_ALLOW': 58897, 'disposition_BLOCK': 1070, 'guard_windows': 98463, 'lease_granted_tokens{org="org-a"}': 56701440, 'lease_refills': 276, 'provider_calls': 58897, 'provider_connections_opened': 10576, 'quota_admitted_tokens{org="org-a"}': 56745448, 'requests_by_round_trips{n="0"}': 59688, 'requests_by_round_trips{n="1"}': 276, 'shared_state_round_trips': 276}
owner counts: {'guard_batches': 59967, 'guard_windows': 98463, 'owner_requests': 59965, 'owner_windows': 98458}
gauges: {'audit_queue_bound': [181], 'guard_tokens_per_s': [25859.570924769152, 26277.94409965762], 'audit_completeness_ratio': [1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'input_queue_cap': [68.0, 69.0], 'guard_queue_cap_tokens': [25859.0, 26277.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 232736, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 232736.13832292237}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 236501, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 236501.49689691857}]
notes: []
