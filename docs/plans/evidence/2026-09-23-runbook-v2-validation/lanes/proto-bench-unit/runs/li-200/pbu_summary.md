# li-200: strict FAIL | load-knee PASS (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 60000 (200.0/s) qualified 58946 (196.49/s) FP-blocks 1054 (0.01757) expected-blocks 0 infra 0 (0.0) drops 0 safety 0 detection-misses 0
by class: {'benign': {'policy_block_fp': 1054, 'qualified': 58946}}
infra reasons: {}
infra detail: {}

T_fw_addon: n=58946 p50=47.6767 p90=54.218 p99=72.5565 p99.9=79.1114 max=110.9836 mean=38.2771
T_fw_addon_nohold: n=58946 p50=10.808 p90=14.3087 p99=18.0378 p99.9=20.0828 max=57.511 mean=10.5997
T_fw_addon_sse: n=41273 p50=50.5106 p90=55.7029 p99=73.1828 p99.9=87.1294 max=110.9836 mean=50.1024
T_fw_addon_json: n=17673 p50=10.8431 p90=14.4005 p99=18.0384 p99.9=20.109 max=45.6514 mean=10.6608
T_addon_first_sse: n=41273 p50=10.7358 p90=14.5103 p99=29.4455 p99.9=35.7836 max=67.8258 mean=10.7932
T_addon_total_sse: n=41273 p50=10.7945 p90=14.2639 p99=18.0378 p99.9=20.0119 max=57.511 mean=10.5735
T_addon_total_json: n=17673 p50=10.8431 p90=14.4005 p99=18.0384 p99.9=20.109 max=45.6514 mean=10.6608
T_release_lag_max: n=41273 p50=50.5106 p90=55.7029 p99=73.1828 p99.9=87.1294 max=110.9836 mean=50.1024
client_ttft_sse: n=41273 p50=160.7765 p90=164.5592 p99=179.5284 p99.9=185.8235 max=217.8661 mean=160.8342
lateness: n=60000 p50=0.0839 p90=0.0929 p99=0.1038 p99.9=0.1227 max=0.3407 mean=0.0838
loadgen: [{'vm': 'rv-pbu-lg-3', 'busy_max': 21.2, 'busy_mean': 7.9, 'late_max_us': 820, 'conn_opens': 518, 'max_inflight': 497}, {'vm': 'rv-pbu-lg-4', 'busy_max': 19.4, 'busy_mean': 7.7, 'late_max_us': 340, 'conn_opens': 521, 'max_inflight': 497}]
wire per client request: {'client_to_gw': 7859.9, 'gw_to_client': 56398.0, 'gw_to_provider': 8595.7, 'provider_to_gw': 57079.1}
gateway cores 7.097 cpu-ms/req 35.605 (workers 31.558, owners 4.045, redis 0.168)
worker util {'n': 18, 'min': 0.269, 'median': 0.35, 'max': 0.407, 'all_sorted': [0.269, 0.298, 0.324, 0.339, 0.339, 0.34, 0.341, 0.346, 0.349, 0.35, 0.361, 0.363, 0.367, 0.369, 0.371, 0.377, 0.38, 0.407]}; per-core schedstat max 0.321 mean 0.301; procstat max 0.435
gpu: {'0': {'samples': 298, 'sm_mean': 29.3, 'sm_max': 54.0, 'mem_mean': 5.3, 'fb_mb_max': 434.0}, '1': {'samples': 298, 'sm_mean': 31.3, 'sm_max': 60.0, 'mem_mean': 5.7, 'fb_mb_max': 434.0}}
rss max total by role (MB): {'redis': 1115.3, 'launcher': 59.7, 'owner': 3065.4, 'worker': 3679.5}; fds {'redis': 0, 'launcher': 7, 'owner': 129, 'worker': 2420}
W t_input_ns: {'n': 59966, 'mean_ms': 9.0584, 'p50_ms': 9.5027, 'p90_ms': 12.6484, 'p99_ms': 16.1874, 'p99.9_ms': 17.9569, 'max_cum_ms': 47.8562}
W t_admit_ns: {'n': 59964, 'mean_ms': 0.0985, 'p50_ms': 0.0927, 'p90_ms': 0.1213, 'p99_ms': 0.1608, 'p99.9_ms': 0.7578, 'max_cum_ms': 3.1884}
W t_tokenize_ns: {'n': 59965, 'mean_ms': 3.8183, 'p50_ms': 3.7519, 'p90_ms': 5.7999, 'p99_ms': 6.914, 'p99.9_ms': 7.6349, 'max_cum_ms': 43.5723}
W t_det_scan_ns: {'n': 59965, 'mean_ms': 0.1189, 'p50_ms': 0.1121, 'p90_ms': 0.169, 'p99_ms': 0.2161, 'p99.9_ms': 0.2509, 'max_cum_ms': 1.1228}
W t_guard_wait_ns: {'n': 59966, 'mean_ms': 4.5916, 'p50_ms': 4.948, 'p90_ms': 7.0451, 'p99_ms': 10.1581, 'p99.9_ms': 11.9931, 'max_cum_ms': 43.2734}
W guard_owner_rtt_ns: {'n': 59966, 'mean_ms': 4.7947, 'p50_ms': 5.1446, 'p90_ms': 7.3073, 'p99_ms': 10.4202, 'p99.9_ms': 12.2552, 'max_cum_ms': 40.4487}
W guard_queue_ns: {'n': 59966, 'mean_ms': 0.4407, 'p50_ms': 0.1142, 'p90_ms': 1.2534, 'p99_ms': 5.2101, 'p99.9_ms': 8.0282, 'max_cum_ms': 12.7038}
W guard_exec_ns: {'n': 59966, 'mean_ms': 3.6063, 'p50_ms': 4.2926, 'p90_ms': 4.5548, 'p99_ms': 6.7174, 'p99.9_ms': 6.8485, 'max_cum_ms': 9.4148}
W dispatch_headers_ns: {'n': 58965, 'mean_ms': 1506.0975, 'p50_ms': 149.9464, 'p90_ms': 5872.0256, 'p99_ms': 7952.4004, 'p99.9_ms': 8153.727, 'max_cum_ms': 8134.3514}
W release_lag_ns: {'n': 9203572, 'mean_ms': 19.8923, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 100.1277}
W holdback_wait_ns: {'n': 8969930, 'mean_ms': 20.3403, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 100.0541}
W release_processing_ns: {'n': 9203572, 'mean_ms': 0.0684, 'p50_ms': 0.0671, 'p90_ms': 0.0916, 'p99_ms': 0.1224, 'p99.9_ms': 0.1628, 'max_cum_ms': 33.8621}
W t_finalize_ns: {'n': 58958, 'mean_ms': 0.2844, 'p50_ms': 0.2806, 'p90_ms': 0.3584, 'p99_ms': 0.4157, 'p99.9_ms': 0.4649, 'max_cum_ms': 3.3822}
W loop_lag_ns: {'n': 53986, 'mean_ms': 0.0755, 'p50_ms': 0.0, 'p90_ms': 0.212, 'p99_ms': 0.9871, 'p99.9_ms': 1.5483, 'max_cum_ms': 37.9964}
W guard_windows_per_request: {'n': 59965, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_exec_ns: {'n': 59966, 'mean_ms': 3.6063, 'p50_ms': 4.2926, 'p90_ms': 4.5548, 'p99_ms': 6.7174, 'p99.9_ms': 6.8485, 'max_cum_ms': 9.4148}
O guard_batch_windows: {'n': 59966, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_queue_ns: {'n': 59966, 'mean_ms': 0.4407, 'p50_ms': 0.1142, 'p90_ms': 1.2534, 'p99_ms': 5.2101, 'p99.9_ms': 8.0282, 'max_cum_ms': 12.7038}
worker counts: {'admitted': 59964, 'audit_enqueued': 118924, 'audit_written': 118925, 'background_round_trips': 10784, 'disposition_ALLOW': 58912, 'disposition_BLOCK': 1054, 'guard_windows': 98460, 'lease_granted_tokens{org="org-a"}': 56701440, 'lease_refills': 276, 'provider_calls': 58912, 'provider_connections_opened': 10635, 'quota_admitted_tokens{org="org-a"}': 56745448, 'requests_by_round_trips{n="0"}': 59688, 'requests_by_round_trips{n="1"}': 276, 'shared_state_round_trips': 276}
owner counts: {'guard_batches': 59966, 'guard_windows': 98460, 'owner_requests': 59965, 'owner_windows': 98458}
gauges: {'audit_queue_bound': [181], 'guard_tokens_per_s': [25859.570924769152, 26277.94409965762], 'audit_completeness_ratio': [1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'input_queue_cap': [68.0, 69.0], 'guard_queue_cap_tokens': [25859.0, 26277.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 232736, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 232736.13832292237}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 236501, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 236501.49689691857}]
notes: []
