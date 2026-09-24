# p22up-200: strict FAIL | load-knee FAIL (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 60460 (201.53/s) qualified 59406 (198.02/s) FP-blocks 1038 (0.01717) expected-blocks 0 infra 16 (0.00026463777704267285) drops 0 safety 0 detection-misses 0
by class: {'benign': {'policy_block_fp': 1038, 'qualified': 59406, 'infra_error': 16}}
infra reasons: {'block_on_unavailable_sem': 16}
infra detail: {'403 http_403 type=policy_violation code=blocked_by_policy': 16}

T_fw_addon: n=59406 p50=12.0159 p90=23.0962 p99=58.2996 p99.9=74.8932 max=94.1014 mean=15.1008
T_fw_addon_nohold: n=59406 p50=11.5383 p90=17.2275 p99=24.9655 p99.9=31.9388 max=40.357 mean=11.8705
T_fw_addon_sse: n=41601 p50=12.2321 p90=37.6165 p99=61.0133 p99.9=76.1399 max=94.1014 mean=16.4467
T_fw_addon_json: n=17805 p50=11.6183 p90=17.3472 p99=24.7323 p99.9=31.5805 max=40.357 mean=11.9561
T_addon_first_sse: n=41601 p50=11.4392 p90=17.5762 p99=31.0995 p99.9=39.9595 max=84.3734 mean=12.034
T_addon_total_sse: n=41601 p50=11.5064 p90=17.1825 p99=25.0731 p99.9=32.223 max=38.2702 mean=11.8338
T_addon_total_json: n=17805 p50=11.6183 p90=17.3472 p99=24.7323 p99.9=31.5805 max=40.357 mean=11.9561
T_release_lag_max: n=4258 p50=51.5092 p90=60.8515 p99=75.928 p99.9=85.7731 max=94.1014 mean=52.1901
client_ttft_sse: n=41601 p50=161.485 p90=167.6204 p99=181.147 p99.9=189.9717 max=234.3769 mean=162.0756
lateness: n=60460 p50=0.0824 p90=0.0928 p99=0.1045 p99.9=0.124 max=0.2453 mean=0.0815
loadgen: [{'vm': 'rv-pbu-lg-1', 'busy_max': 11.6, 'busy_mean': 8.2, 'late_max_us': 199, 'conn_opens': 558, 'max_inflight': 532}, {'vm': 'rv-pbu-lg-2', 'busy_max': 8.8, 'busy_mean': 7.8, 'late_max_us': 319, 'conn_opens': 539, 'max_inflight': 529}]
wire per client request: {'client_to_gw': 8218.7, 'gw_to_client': 56406.8, 'gw_to_provider': 8893.3, 'provider_to_gw': 57086.6}
gateway cores 7.163 cpu-ms/req 35.659 (workers 31.604, owners 4.053, redis 0.165)
worker util {'n': 18, 'min': 0.3, 'median': 0.358, 'max': 0.397}; per-core schedstat max 0.323 mean 0.304; procstat max 0.414
gpu: {'0': {'samples': 298, 'sm_mean': 30.9, 'sm_max': 62.0, 'mem_mean': 5.6, 'fb_mb_max': 434.0}, '1': {'samples': 298, 'sm_mean': 31.3, 'sm_max': 64.0, 'mem_mean': 5.7, 'fb_mb_max': 434.0}}
rss max total by role (MB): {'redis': 1576.1, 'launcher': 59.8, 'owner': 3065.4, 'worker': 3665.1}; fds {'redis': 0, 'launcher': 7, 'owner': 131, 'worker': 2519}
W t_input_ns: {'n': 60412, 'mean_ms': 9.9471, 'p50_ms': 9.8959, 'p90_ms': 14.7456, 'p99_ms': 21.6269, 'p99.9_ms': 27.6562, 'max_cum_ms': 58.2371}
W t_admit_ns: {'n': 60411, 'mean_ms': 0.0973, 'p50_ms': 0.0916, 'p90_ms': 0.1183, 'p99_ms': 0.1526, 'p99.9_ms': 0.7578, 'max_cum_ms': 12.1347}
W t_tokenize_ns: {'n': 60411, 'mean_ms': 3.4718, 'p50_ms': 3.457, 'p90_ms': 5.5378, 'p99_ms': 6.6519, 'p99.9_ms': 7.3728, 'max_cum_ms': 10.1578}
W t_det_scan_ns: {'n': 60411, 'mean_ms': 0.1214, 'p50_ms': 0.1162, 'p90_ms': 0.1669, 'p99_ms': 0.212, 'p99.9_ms': 0.257, 'max_cum_ms': 3.5839}
W t_guard_wait_ns: {'n': 60412, 'mean_ms': 5.7928, 'p50_ms': 5.1446, 'p90_ms': 9.3716, 'p99_ms': 15.9252, 'p99.9_ms': 21.889, 'max_cum_ms': 51.9025}
W guard_owner_rtt_ns: {'n': 60412, 'mean_ms': 5.9453, 'p50_ms': 5.4067, 'p90_ms': 9.5027, 'p99_ms': 15.6631, 'p99.9_ms': 21.6269, 'max_cum_ms': 49.3479}
W guard_queue_ns: {'n': 60396, 'mean_ms': 1.2888, 'p50_ms': 0.1306, 'p90_ms': 4.2271, 'p99_ms': 10.1581, 'p99.9_ms': 15.9252, 'max_cum_ms': 19.3704}
W guard_exec_ns: {'n': 60396, 'mean_ms': 3.6165, 'p50_ms': 4.2926, 'p90_ms': 4.5548, 'p99_ms': 6.7174, 'p99.9_ms': 6.9796, 'max_cum_ms': 8.1247}
W dispatch_headers_ns: {'n': 59378, 'mean_ms': 1504.3346, 'p50_ms': 149.9464, 'p90_ms': 5872.0256, 'p99_ms': 7952.4004, 'p99.9_ms': 8153.727, 'max_cum_ms': 8141.2944}
W release_lag_ns: {'n': 9273170, 'mean_ms': 19.8884, 'p50_ms': 20.054, 'p90_ms': 20.3162, 'p99_ms': 40.108, 'p99.9_ms': 41.6809, 'max_cum_ms': 100.1455}
W holdback_wait_ns: {'n': 9037151, 'mean_ms': 20.3392, 'p50_ms': 20.054, 'p90_ms': 20.3162, 'p99_ms': 40.108, 'p99.9_ms': 41.6809, 'max_cum_ms': 100.0931}
W release_processing_ns: {'n': 9273170, 'mean_ms': 0.0669, 'p50_ms': 0.0653, 'p90_ms': 0.0896, 'p99_ms': 0.1203, 'p99.9_ms': 0.1526, 'max_cum_ms': 35.97}
W t_finalize_ns: {'n': 59386, 'mean_ms': 0.2809, 'p50_ms': 0.2765, 'p90_ms': 0.3543, 'p99_ms': 0.4116, 'p99.9_ms': 0.4526, 'max_cum_ms': 3.0203}
W loop_lag_ns: {'n': 53490, 'mean_ms': 0.9694, 'p50_ms': 0.0157, 'p90_ms': 4.1124, 'p99_ms': 10.5513, 'p99.9_ms': 13.4349, 'max_cum_ms': 44.0351}
W guard_windows_per_request: {'n': 60411, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_exec_ns: {'n': 60359, 'mean_ms': 3.6164, 'p50_ms': 4.2926, 'p90_ms': 4.5548, 'p99_ms': 6.7174, 'p99.9_ms': 6.9796, 'max_cum_ms': 8.1247}
O guard_batch_windows: {'n': 60359, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_queue_ns: {'n': 60359, 'mean_ms': 1.2889, 'p50_ms': 0.1306, 'p90_ms': 4.2271, 'p99_ms': 10.1581, 'p99.9_ms': 15.9252, 'max_cum_ms': 19.3704}
worker counts: {'admitted': 60411, 'audit_enqueued': 119798, 'audit_written': 119800, 'background_round_trips': 10698, 'disposition_ALLOW': 59358, 'disposition_BLOCK': 1054, 'guard_deadline_expired': 16, 'guard_unavailable_findings': 16, 'guard_windows': 99136, 'lease_granted_tokens{org="org-a"}': 56701440, 'lease_refills': 276, 'provider_calls': 59358, 'provider_connections_opened': 10315, 'quota_admitted_tokens{org="org-a"}': 57141213, 'requests_by_round_trips{n="0"}': 60135, 'requests_by_round_trips{n="1"}': 276, 'shared_state_round_trips': 276}
owner counts: {'guard_batches': 60359, 'guard_deadline_expired': 16, 'guard_windows': 99072, 'owner_requests': 60375, 'owner_windows': 99100}
gauges: {'audit_queue_bound': [181], 'audit_completeness_ratio': [1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'guard_tokens_per_s': [25883.361277508728, 26080.30872146161], 'input_queue_cap': [68.0], 'guard_queue_cap_tokens': [25883.0, 26080.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 1024, 'owner_queue_cap_tokens': 232950, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 232950.25149757855}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 234722, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 234722.77849315447}]
notes: []
