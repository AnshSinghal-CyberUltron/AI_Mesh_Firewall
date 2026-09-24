# p22u-200: strict FAIL | load-knee FAIL (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 60000 (200.0/s) qualified 58917 (196.39/s) FP-blocks 1083 (0.01805) expected-blocks 0 infra 0 (0.0) drops 0 safety 0 detection-misses 0
by class: {'benign': {'policy_block_fp': 1083, 'qualified': 58917}}
infra reasons: {}
infra detail: {}

T_fw_addon: n=58917 p50=11.1771 p90=19.1814 p99=56.3498 p99.9=72.9635 max=103.8607 mean=13.9581
T_fw_addon_nohold: n=58917 p50=10.7553 p90=15.1714 p99=20.8489 p99.9=26.8261 max=36.8184 mean=10.7723
T_fw_addon_sse: n=41249 p50=11.376 p90=34.0361 p99=58.5972 p99.9=74.3072 max=103.8607 mean=15.2929
T_fw_addon_json: n=17668 p50=10.8063 p90=15.2857 p99=20.7511 p99.9=26.6192 max=36.8184 mean=10.842
T_addon_first_sse: n=41249 p50=10.6769 p90=15.4119 p99=29.6659 p99.9=36.8327 max=70.023 mean=10.9694
T_addon_total_sse: n=41249 p50=10.7347 p90=15.1189 p99=20.8945 p99.9=26.8882 max=35.6824 mean=10.7425
T_addon_total_json: n=17668 p50=10.8063 p90=15.2857 p99=20.7511 p99.9=26.6192 max=36.8184 mean=10.842
T_release_lag_max: n=4112 p50=50.7999 p90=58.5783 p99=74.3072 p99.9=93.2003 max=103.8607 mean=51.3361
client_ttft_sse: n=41249 p50=160.7177 p90=165.4506 p99=179.7266 p99.9=186.8424 max=220.0623 mean=161.0102
lateness: n=60000 p50=0.0846 p90=0.0945 p99=0.1067 p99.9=0.1312 max=0.3569 mean=0.085
loadgen: [{'vm': 'rv-pbu-lg-1', 'busy_max': 21.1, 'busy_mean': 7.7, 'late_max_us': 1747, 'conn_opens': 519, 'max_inflight': 497}, {'vm': 'rv-pbu-lg-2', 'busy_max': 20.2, 'busy_mean': 7.5, 'late_max_us': 586, 'conn_opens': 521, 'max_inflight': 497}]
wire per client request: {'client_to_gw': 8047.7, 'gw_to_client': 56368.3, 'gw_to_provider': 8568.8, 'provider_to_gw': 57050.2}
gateway cores 7.041 cpu-ms/req 35.323 (workers 31.276, owners 4.045, redis 0.164)
worker util {'n': 18, 'min': 0.266, 'median': 0.356, 'max': 0.414}; per-core schedstat max 0.323 mean 0.301; procstat max 0.413
gpu: {'0': {'samples': 298, 'sm_mean': 30.5, 'sm_max': 53.0, 'mem_mean': 5.5, 'fb_mb_max': 434.0}, '1': {'samples': 298, 'sm_mean': 30.5, 'sm_max': 60.0, 'mem_mean': 5.5, 'fb_mb_max': 434.0}}
rss max total by role (MB): {'redis': 651.0, 'launcher': 59.8, 'owner': 3064.2, 'worker': 3653.9}; fds {'redis': 0, 'launcher': 7, 'owner': 126, 'worker': 2426}
W t_input_ns: {'n': 60000, 'mean_ms': 8.9561, 'p50_ms': 9.2406, 'p90_ms': 13.0417, 'p99_ms': 17.4326, 'p99.9_ms': 22.1512, 'max_cum_ms': 35.0298}
W t_admit_ns: {'n': 60000, 'mean_ms': 0.0983, 'p50_ms': 0.0927, 'p90_ms': 0.1193, 'p99_ms': 0.1567, 'p99.9_ms': 0.8397, 'max_cum_ms': 6.231}
W t_tokenize_ns: {'n': 60000, 'mean_ms': 3.4401, 'p50_ms': 3.457, 'p90_ms': 5.4067, 'p99_ms': 6.4553, 'p99.9_ms': 7.1107, 'max_cum_ms': 7.98}
W t_det_scan_ns: {'n': 60000, 'mean_ms': 0.1186, 'p50_ms': 0.1111, 'p90_ms': 0.169, 'p99_ms': 0.214, 'p99.9_ms': 0.2488, 'max_cum_ms': 3.4273}
W t_guard_wait_ns: {'n': 60000, 'mean_ms': 4.8502, 'p50_ms': 5.0135, 'p90_ms': 7.3073, 'p99_ms': 11.862, 'p99.9_ms': 16.4495, 'max_cum_ms': 29.0938}
W guard_owner_rtt_ns: {'n': 60000, 'mean_ms': 5.0059, 'p50_ms': 5.2101, 'p90_ms': 7.5694, 'p99_ms': 11.3377, 'p99.9_ms': 14.8767, 'max_cum_ms': 22.657}
W guard_queue_ns: {'n': 60000, 'mean_ms': 0.4333, 'p50_ms': 0.1132, 'p90_ms': 1.2698, 'p99_ms': 5.079, 'p99.9_ms': 8.0937, 'max_cum_ms': 12.3749}
W guard_exec_ns: {'n': 60000, 'mean_ms': 3.5994, 'p50_ms': 4.2926, 'p90_ms': 4.5548, 'p99_ms': 6.6519, 'p99.9_ms': 6.8485, 'max_cum_ms': 8.0825}
W dispatch_headers_ns: {'n': 58973, 'mean_ms': 1506.7282, 'p50_ms': 149.9464, 'p90_ms': 5872.0256, 'p99_ms': 7952.4004, 'p99.9_ms': 8153.727, 'max_cum_ms': 8136.8244}
W release_lag_ns: {'n': 9209272, 'mean_ms': 19.8907, 'p50_ms': 20.054, 'p90_ms': 20.3162, 'p99_ms': 40.108, 'p99.9_ms': 41.6809, 'max_cum_ms': 100.1049}
W holdback_wait_ns: {'n': 8975485, 'mean_ms': 20.3403, 'p50_ms': 20.054, 'p90_ms': 20.3162, 'p99_ms': 40.108, 'p99.9_ms': 41.6809, 'max_cum_ms': 100.0078}
W release_processing_ns: {'n': 9209272, 'mean_ms': 0.0668, 'p50_ms': 0.0653, 'p90_ms': 0.0906, 'p99_ms': 0.1203, 'p99.9_ms': 0.1526, 'max_cum_ms': 3.7107}
W t_finalize_ns: {'n': 58999, 'mean_ms': 0.282, 'p50_ms': 0.2765, 'p90_ms': 0.3584, 'p99_ms': 0.4157, 'p99.9_ms': 0.4608, 'max_cum_ms': 3.0203}
W loop_lag_ns: {'n': 53600, 'mean_ms': 0.9157, 'p50_ms': 0.0163, 'p90_ms': 3.8175, 'p99_ms': 9.8959, 'p99.9_ms': 12.7795, 'max_cum_ms': 27.3658}
W guard_windows_per_request: {'n': 60000, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_exec_ns: {'n': 60004, 'mean_ms': 3.5996, 'p50_ms': 4.2926, 'p90_ms': 4.5548, 'p99_ms': 6.6519, 'p99.9_ms': 6.8485, 'max_cum_ms': 8.0825}
O guard_batch_windows: {'n': 60004, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_queue_ns: {'n': 60004, 'mean_ms': 0.4332, 'p50_ms': 0.1132, 'p90_ms': 1.2698, 'p99_ms': 5.079, 'p99.9_ms': 8.0937, 'max_cum_ms': 12.3749}
worker counts: {'admitted': 60000, 'audit_enqueued': 118999, 'audit_written': 118998, 'background_round_trips': 10720, 'disposition_ALLOW': 58917, 'disposition_BLOCK': 1083, 'guard_windows': 98514, 'lease_granted_tokens{org="org-a"}': 56290560, 'lease_refills': 274, 'provider_calls': 58917, 'provider_connections_opened': 9758, 'quota_admitted_tokens{org="org-a"}': 56780404, 'requests_by_round_trips{n="0"}': 59726, 'requests_by_round_trips{n="1"}': 274, 'shared_state_round_trips': 274}
owner counts: {'guard_batches': 60004, 'guard_windows': 98524, 'owner_requests': 60003, 'owner_windows': 98523}
gauges: {'audit_queue_bound': [181], 'audit_completeness_ratio': [0.9999116295510782, 1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'guard_tokens_per_s': [25883.361277508728, 26080.30872146161], 'input_queue_cap': [68.0], 'guard_queue_cap_tokens': [25883.0, 26080.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 232950, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 232950.25149757855}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 234722, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 234722.77849315447}]
notes: []
