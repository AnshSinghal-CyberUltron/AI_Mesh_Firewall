# li-150: strict FAIL | load-knee PASS (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 45000 (150.0/s) qualified 44223 (147.41/s) FP-blocks 777 (0.01727) expected-blocks 0 infra 0 (0.0) drops 0 safety 0 detection-misses 0
by class: {'benign': {'qualified': 44223, 'policy_block_fp': 777}}
infra reasons: {}
infra detail: {}

T_fw_addon: n=44223 p50=46.9241 p90=52.7317 p99=71.5558 p99.9=75.8964 max=108.6207 mean=37.436
T_fw_addon_nohold: n=44223 p50=10.4442 p90=12.9791 p99=16.1855 p99.9=17.7979 max=31.0099 mean=9.8364
T_fw_addon_sse: n=30958 p50=50.1215 p90=54.3518 p99=72.0508 p99.9=85.1628 max=108.6207 mean=49.2267
T_fw_addon_json: n=13265 p50=10.5094 p90=13.1236 p99=16.234 p99.9=17.8225 max=31.0099 mean=9.9187
T_addon_first_sse: n=30958 p50=10.3592 p90=13.1582 p99=28.1801 p99.9=34.5356 max=52.7082 mean=10.0342
T_addon_total_sse: n=30958 p50=10.4222 p90=12.9133 p99=16.168 p99.9=17.7979 max=19.6087 mean=9.8012
T_addon_total_json: n=13265 p50=10.5094 p90=13.1236 p99=16.234 p99.9=17.8225 max=31.0099 mean=9.9187
T_release_lag_max: n=30958 p50=50.1215 p90=54.3518 p99=72.0508 p99.9=85.1628 max=108.6207 mean=49.2267
client_ttft_sse: n=30958 p50=160.3996 p90=163.1981 p99=178.1939 p99.9=184.6158 max=202.7581 mean=160.0764
lateness: n=45000 p50=0.0829 p90=0.0918 p99=0.1025 p99.9=0.1269 max=0.3051 mean=0.083
loadgen: [{'vm': 'rv-pbu-lg-3', 'busy_max': 9.4, 'busy_mean': 6.3, 'late_max_us': 781, 'conn_opens': 390, 'max_inflight': 378}, {'vm': 'rv-pbu-lg-4', 'busy_max': 6.5, 'busy_mean': 6.0, 'late_max_us': 305, 'conn_opens': 391, 'max_inflight': 377}]
wire per client request: {'client_to_gw': 7869.4, 'gw_to_client': 56335.8, 'gw_to_provider': 8673.9, 'provider_to_gw': 57017.1}
gateway cores 5.232 cpu-ms/req 34.999 (workers 30.971, owners 4.024, redis 0.172)
worker util {'n': 18, 'min': 0.195, 'median': 0.254, 'max': 0.322, 'all_sorted': [0.195, 0.202, 0.219, 0.23, 0.242, 0.242, 0.243, 0.247, 0.253, 0.254, 0.263, 0.27, 0.27, 0.276, 0.289, 0.301, 0.314, 0.322]}; per-core schedstat max 0.234 mean 0.223; procstat max 0.374
gpu: {'0': {'samples': 298, 'sm_mean': 23.4, 'sm_max': 55.0, 'mem_mean': 4.3, 'fb_mb_max': 434.0}, '1': {'samples': 298, 'sm_mean': 22.1, 'sm_max': 43.0, 'mem_mean': 4.0, 'fb_mb_max': 434.0}}
rss max total by role (MB): {'redis': 708.1, 'launcher': 59.7, 'owner': 3065.3, 'worker': 3669.2}; fds {'redis': 0, 'launcher': 7, 'owner': 129, 'worker': 1930}
W t_input_ns: {'n': 44972, 'mean_ms': 8.4458, 'p50_ms': 9.1095, 'p90_ms': 11.3377, 'p99_ms': 14.4835, 'p99.9_ms': 15.532, 'max_cum_ms': 26.5397}
W t_admit_ns: {'n': 44971, 'mean_ms': 0.0961, 'p50_ms': 0.0906, 'p90_ms': 0.1172, 'p99_ms': 0.1505, 'p99.9_ms': 0.684, 'max_cum_ms': 3.1884}
W t_tokenize_ns: {'n': 44972, 'mean_ms': 3.6162, 'p50_ms': 3.6536, 'p90_ms': 5.4067, 'p99_ms': 6.2587, 'p99.9_ms': 7.1107, 'max_cum_ms': 20.3992}
W t_det_scan_ns: {'n': 44972, 'mean_ms': 0.112, 'p50_ms': 0.1091, 'p90_ms': 0.1464, 'p99_ms': 0.1915, 'p99.9_ms': 0.2263, 'max_cum_ms': 1.1228}
W t_guard_wait_ns: {'n': 44972, 'mean_ms': 4.232, 'p50_ms': 4.8169, 'p90_ms': 5.2756, 'p99_ms': 7.9626, 'p99.9_ms': 9.5027, 'max_cum_ms': 11.0069}
W guard_owner_rtt_ns: {'n': 44972, 'mean_ms': 4.4322, 'p50_ms': 5.079, 'p90_ms': 5.5378, 'p99_ms': 8.2248, 'p99.9_ms': 9.7649, 'max_cum_ms': 11.4114}
W guard_queue_ns: {'n': 44972, 'mean_ms': 0.1637, 'p50_ms': 0.105, 'p90_ms': 0.1403, 'p99_ms': 2.007, 'p99.9_ms': 4.0796, 'max_cum_ms': 5.8036}
W guard_exec_ns: {'n': 44972, 'mean_ms': 3.5859, 'p50_ms': 4.2926, 'p90_ms': 4.4892, 'p99_ms': 6.6519, 'p99.9_ms': 6.783, 'max_cum_ms': 9.4148}
W dispatch_headers_ns: {'n': 44213, 'mean_ms': 1503.0513, 'p50_ms': 149.9464, 'p90_ms': 5804.9167, 'p99_ms': 7952.4004, 'p99.9_ms': 8153.727, 'max_cum_ms': 8132.2612}
W release_lag_ns: {'n': 6873091, 'mean_ms': 19.8909, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 100.1155}
W holdback_wait_ns: {'n': 6698506, 'mean_ms': 20.3407, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 100.0541}
W release_processing_ns: {'n': 6873091, 'mean_ms': 0.0668, 'p50_ms': 0.0653, 'p90_ms': 0.0876, 'p99_ms': 0.1172, 'p99.9_ms': 0.1546, 'max_cum_ms': 2.9159}
W t_finalize_ns: {'n': 44183, 'mean_ms': 0.2728, 'p50_ms': 0.2683, 'p90_ms': 0.342, 'p99_ms': 0.4035, 'p99.9_ms': 0.4444, 'max_cum_ms': 2.0778}
W loop_lag_ns: {'n': 53987, 'mean_ms': 0.0686, 'p50_ms': 0.0, 'p90_ms': 0.1751, 'p99_ms': 1.0035, 'p99.9_ms': 1.5319, 'max_cum_ms': 12.7589}
W guard_windows_per_request: {'n': 44972, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_exec_ns: {'n': 44972, 'mean_ms': 3.5859, 'p50_ms': 4.2926, 'p90_ms': 4.4892, 'p99_ms': 6.6519, 'p99.9_ms': 6.783, 'max_cum_ms': 9.4148}
O guard_batch_windows: {'n': 44972, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_queue_ns: {'n': 44972, 'mean_ms': 0.1637, 'p50_ms': 0.105, 'p90_ms': 0.1403, 'p99_ms': 2.007, 'p99.9_ms': 4.0796, 'max_cum_ms': 5.8036}
worker counts: {'admitted': 44971, 'audit_enqueued': 89155, 'audit_written': 89155, 'background_round_trips': 10788, 'disposition_ALLOW': 44195, 'disposition_BLOCK': 777, 'guard_windows': 73874, 'lease_granted_tokens{org="org-a"}': 42731520, 'lease_refills': 208, 'provider_calls': 44195, 'provider_connections_opened': 8400, 'quota_admitted_tokens{org="org-a"}': 42524962, 'requests_by_round_trips{n="0"}': 44763, 'requests_by_round_trips{n="1"}': 208, 'shared_state_round_trips': 208}
owner counts: {'guard_batches': 44972, 'guard_windows': 73874, 'owner_requests': 44972, 'owner_windows': 73874}
gauges: {'audit_queue_bound': [181], 'guard_tokens_per_s': [25859.570924769152, 26277.94409965762], 'audit_completeness_ratio': [1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'input_queue_cap': [68.0, 69.0], 'guard_queue_cap_tokens': [25859.0, 26277.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 512, 'owner_queue_cap_tokens': 232736, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 232736.13832292237}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 236501, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 236501.49689691857}]
notes: []
