# hb-on-30: strict FAIL | load-knee PASS (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 12000 (40.0/s) qualified 11819 (39.4/s) FP-blocks 181 (0.01508) expected-blocks 0 infra 0 (0.0) drops 0 safety 0 detection-misses 0
by class: {'benign': {'policy_block_fp': 181, 'qualified': 11819}}
infra reasons: {}
infra detail: {}

T_fw_addon: n=11819 p50=69.3677 p90=73.5868 p99=101.2335 p99.9=128.8239 max=159.1405 mean=68.6068
T_fw_addon_nohold: n=11819 p50=9.6238 p90=12.3539 p99=14.9943 p99.9=18.5161 max=20.6955 mean=9.1077
T_fw_addon_sse: n=11819 p50=69.3677 p90=73.5868 p99=101.2335 p99.9=128.8239 max=159.1405 mean=68.6068
T_fw_addon_json: n=0
T_addon_first_sse: n=11819 p50=9.5503 p90=12.8661 p99=39.5084 p99.9=43.6196 max=70.3945 mean=9.5461
T_addon_total_sse: n=11819 p50=9.6238 p90=12.3539 p99=14.9943 p99.9=18.5161 max=20.6955 mean=9.1077
T_addon_total_json: n=0
T_release_lag_max: n=11819 p50=69.3677 p90=73.5868 p99=101.2335 p99.9=128.8239 max=159.1405 mean=68.6068
client_ttft_sse: n=11819 p50=159.6044 p90=162.9244 p99=189.5218 p99.9=193.6719 max=220.4016 mean=159.5939
lateness: n=12000 p50=0.0867 p90=0.0968 p99=0.1091 p99.9=0.1388 max=0.2333 mean=0.0871
loadgen: [{'vm': 'rv-pbu-lg-1', 'busy_max': 16.8, 'busy_mean': 4.9, 'late_max_us': 327, 'conn_opens': 165, 'max_inflight': 162}, {'vm': 'rv-pbu-lg-2', 'busy_max': 16.5, 'busy_mean': 5.0, 'late_max_us': 254, 'conn_opens': 165, 'max_inflight': 162}]
wire per client request: {'client_to_gw': 13116.5, 'gw_to_client': 80098.5, 'gw_to_provider': 12735.8, 'provider_to_gw': 81092.0}
gateway cores 1.91 cpu-ms/req 47.908 (workers 43.731, owners 4.166, redis 0.241)
worker util {'n': 18, 'min': 0.047, 'median': 0.096, 'max': 0.157}; per-core schedstat max 0.098 mean 0.082; procstat max 0.179
gpu: {'0': {'samples': 298, 'sm_mean': 6.4, 'sm_max': 19.0, 'mem_mean': 1.2, 'fb_mb_max': 434.0}, '1': {'samples': 298, 'sm_mean': 6.0, 'sm_max': 18.0, 'mem_mean': 1.1, 'fb_mb_max': 434.0}}
rss max total by role (MB): {'redis': 286.0, 'launcher': 59.8, 'owner': 3066.9, 'worker': 3630.5}; fds {'redis': 0, 'launcher': 7, 'owner': 131, 'worker': 1094}
W t_input_ns: {'n': 11988, 'mean_ms': 7.7277, 'p50_ms': 8.4541, 'p90_ms': 10.4202, 'p99_ms': 12.9106, 'p99.9_ms': 16.0563, 'max_cum_ms': 19.6548}
W t_admit_ns: {'n': 11987, 'mean_ms': 0.0948, 'p50_ms': 0.0896, 'p90_ms': 0.1111, 'p99_ms': 0.1485, 'p99.9_ms': 0.6267, 'max_cum_ms': 2.9529}
W t_tokenize_ns: {'n': 11988, 'mean_ms': 2.868, 'p50_ms': 2.9, 'p90_ms': 4.3581, 'p99_ms': 5.0135, 'p99.9_ms': 6.1932, 'max_cum_ms': 7.5195}
W t_det_scan_ns: {'n': 11988, 'mean_ms': 0.1036, 'p50_ms': 0.1009, 'p90_ms': 0.1362, 'p99_ms': 0.1853, 'p99.9_ms': 0.2202, 'max_cum_ms': 1.6687}
W t_guard_wait_ns: {'n': 11988, 'mean_ms': 4.2918, 'p50_ms': 4.8824, 'p90_ms': 5.2756, 'p99_ms': 7.4383, 'p99.9_ms': 11.2067, 'max_cum_ms': 13.8031}
W guard_owner_rtt_ns: {'n': 11988, 'mean_ms': 4.4672, 'p50_ms': 5.079, 'p90_ms': 5.5378, 'p99_ms': 7.7005, 'p99.9_ms': 10.5513, 'max_cum_ms': 13.1421}
W guard_queue_ns: {'n': 11988, 'mean_ms': 0.1159, 'p50_ms': 0.1132, 'p90_ms': 0.1403, 'p99_ms': 0.1731, 'p99.9_ms': 0.2099, 'max_cum_ms': 1.5043}
W guard_exec_ns: {'n': 11988, 'mean_ms': 3.634, 'p50_ms': 4.2926, 'p90_ms': 4.5548, 'p99_ms': 6.6519, 'p99.9_ms': 6.783, 'max_cum_ms': 9.2784}
W dispatch_headers_ns: {'n': 11810, 'mean_ms': 150.8584, 'p50_ms': 149.9464, 'p90_ms': 152.0435, 'p99_ms': 152.0435, 'p99.9_ms': 156.2378, 'max_cum_ms': 164.6999}
W release_lag_ns: {'n': 2631515, 'mean_ms': 29.7992, 'p50_ms': 30.0155, 'p90_ms': 30.0155, 'p99_ms': 60.031, 'p99.9_ms': 60.031, 'max_cum_ms': 150.076}
W holdback_wait_ns: {'n': 2564669, 'mean_ms': 30.5086, 'p50_ms': 30.0155, 'p90_ms': 30.0155, 'p99_ms': 60.031, 'p99.9_ms': 60.031, 'max_cum_ms': 150.009}
W release_processing_ns: {'n': 2631515, 'mean_ms': 0.0657, 'p50_ms': 0.0653, 'p90_ms': 0.0835, 'p99_ms': 0.108, 'p99.9_ms': 0.1382, 'max_cum_ms': 1.4524}
W t_finalize_ns: {'n': 11808, 'mean_ms': 0.269, 'p50_ms': 0.2611, 'p90_ms': 0.3174, 'p99_ms': 0.3789, 'p99.9_ms': 0.428, 'max_cum_ms': 0.7054}
W loop_lag_ns: {'n': 53640, 'mean_ms': 0.7464, 'p50_ms': 0.1976, 'p90_ms': 1.876, 'p99_ms': 6.783, 'p99.9_ms': 8.4541, 'max_cum_ms': 36.2431}
W guard_windows_per_request: {'n': 11988, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_exec_ns: {'n': 12004, 'mean_ms': 3.6334, 'p50_ms': 4.2926, 'p90_ms': 4.5548, 'p99_ms': 6.6519, 'p99.9_ms': 6.783, 'max_cum_ms': 9.2784}
O guard_batch_windows: {'n': 12004, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_queue_ns: {'n': 12004, 'mean_ms': 0.1159, 'p50_ms': 0.1132, 'p90_ms': 0.1403, 'p99_ms': 0.1731, 'p99.9_ms': 0.2079, 'max_cum_ms': 1.5043}
worker counts: {'admitted': 11987, 'audit_enqueued': 23796, 'audit_written': 23795, 'background_round_trips': 10728, 'disposition_ALLOW': 11807, 'disposition_BLOCK': 181, 'guard_windows': 19729, 'lease_granted_tokens{org="org-a"}': 10888320, 'lease_refills': 53, 'provider_calls': 11807, 'provider_connections_opened': 2573, 'quota_admitted_tokens{org="org-a"}': 11384691, 'requests_by_round_trips{n="0"}': 11934, 'requests_by_round_trips{n="1"}': 53, 'shared_state_round_trips': 53}
owner counts: {'guard_batches': 12004, 'guard_windows': 19752, 'owner_requests': 12004, 'owner_windows': 19752}
gauges: {'audit_queue_bound': [181], 'audit_completeness_ratio': [0.9998122065727699, 1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'guard_tokens_per_s': [26013.467373822015, 26032.753479556235], 'input_queue_cap': [2.0], 'guard_queue_cap_tokens': [520.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4682, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 234121.20636439815}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4685, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 234294.78131600612}]
notes: []
