# hb-off-30: strict FAIL | load-knee PASS (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 12000 (40.0/s) qualified 11822 (39.41/s) FP-blocks 178 (0.01483) expected-blocks 0 infra 0 (0.0) drops 0 safety 0 detection-misses 0
by class: {'benign': {'policy_block_fp': 178, 'qualified': 11822}}
infra reasons: {}
infra detail: {}

T_fw_addon: n=11822 p50=12.8341 p90=17.0656 p99=20.5557 p99.9=24.4949 max=30.8756 mean=13.0927
T_fw_addon_nohold: n=11822 p50=9.5504 p90=12.0928 p99=14.6991 p99.9=17.9434 max=23.5264 mean=9.0001
T_fw_addon_sse: n=11822 p50=12.8341 p90=17.0656 p99=20.5557 p99.9=24.4949 max=30.8756 mean=13.0927
T_fw_addon_json: n=0
T_addon_first_sse: n=11822 p50=9.4606 p90=11.9471 p99=14.4321 p99.9=17.8532 max=23.4522 mean=8.9024
T_addon_total_sse: n=11822 p50=9.5504 p90=12.0928 p99=14.6991 p99.9=17.9434 max=23.5264 mean=9.0001
T_addon_total_json: n=0
T_release_lag_max: n=11822 p50=12.8341 p90=17.0656 p99=20.5557 p99.9=24.4949 max=30.8756 mean=13.0927
client_ttft_sse: n=11822 p50=159.51 p90=162.0091 p99=164.5049 p99.9=167.9304 max=173.5114 mean=158.9505
lateness: n=12000 p50=0.0874 p90=0.0973 p99=0.11 p99.9=0.1284 max=0.1971 mean=0.0871
loadgen: [{'vm': 'rv-pbu-lg-1', 'busy_max': 14.6, 'busy_mean': 5.0, 'late_max_us': 380, 'conn_opens': 165, 'max_inflight': 162}, {'vm': 'rv-pbu-lg-2', 'busy_max': 17.1, 'busy_mean': 4.7, 'late_max_us': 341, 'conn_opens': 166, 'max_inflight': 162}]
wire per client request: {'client_to_gw': 10616.2, 'gw_to_client': 81157.7, 'gw_to_provider': 12690.8, 'provider_to_gw': 81098.1}
gateway cores 1.648 cpu-ms/req 41.34 (workers 37.171, owners 4.158, redis 0.243)
worker util {'n': 18, 'min': 0.054, 'median': 0.082, 'max': 0.113}; per-core schedstat max 0.08 mean 0.072; procstat max 0.18
gpu: {'0': {'samples': 298, 'sm_mean': 6.2, 'sm_max': 17.0, 'mem_mean': 1.1, 'fb_mb_max': 434.0}, '1': {'samples': 298, 'sm_mean': 5.8, 'sm_max': 18.0, 'mem_mean': 1.0, 'fb_mb_max': 434.0}}
rss max total by role (MB): {'redis': 285.9, 'launcher': 59.7, 'owner': 3065.6, 'worker': 3629.6}; fds {'redis': 0, 'launcher': 7, 'owner': 129, 'worker': 1092}
W t_input_ns: {'n': 11989, 'mean_ms': 7.6963, 'p50_ms': 8.4541, 'p90_ms': 10.2892, 'p99_ms': 12.7795, 'p99.9_ms': 15.7942, 'max_cum_ms': 23.6637}
W t_admit_ns: {'n': 11989, 'mean_ms': 0.0968, 'p50_ms': 0.0916, 'p90_ms': 0.1132, 'p99_ms': 0.1526, 'p99.9_ms': 0.6103, 'max_cum_ms': 3.1107}
W t_tokenize_ns: {'n': 11989, 'mean_ms': 2.8424, 'p50_ms': 2.8672, 'p90_ms': 4.2926, 'p99_ms': 4.948, 'p99.9_ms': 6.5864, 'max_cum_ms': 7.3783}
W t_det_scan_ns: {'n': 11989, 'mean_ms': 0.1145, 'p50_ms': 0.1111, 'p90_ms': 0.1464, 'p99_ms': 0.1976, 'p99.9_ms': 0.2406, 'max_cum_ms': 0.8606}
W t_guard_wait_ns: {'n': 11989, 'mean_ms': 4.2758, 'p50_ms': 4.8824, 'p90_ms': 5.2756, 'p99_ms': 7.3728, 'p99.9_ms': 11.3377, 'max_cum_ms': 18.3214}
W guard_owner_rtt_ns: {'n': 11989, 'mean_ms': 4.4595, 'p50_ms': 5.079, 'p90_ms': 5.5378, 'p99_ms': 7.6349, 'p99.9_ms': 10.027, 'max_cum_ms': 18.0896}
W guard_queue_ns: {'n': 11989, 'mean_ms': 0.1158, 'p50_ms': 0.1132, 'p90_ms': 0.1382, 'p99_ms': 0.169, 'p99.9_ms': 0.2181, 'max_cum_ms': 1.3489}
W guard_exec_ns: {'n': 11989, 'mean_ms': 3.6223, 'p50_ms': 4.2926, 'p90_ms': 4.5548, 'p99_ms': 6.6519, 'p99.9_ms': 6.7174, 'max_cum_ms': 8.8594}
W dispatch_headers_ns: {'n': 11812, 'mean_ms': 150.8454, 'p50_ms': 149.9464, 'p90_ms': 152.0435, 'p99_ms': 152.0435, 'p99.9_ms': 156.2378, 'max_cum_ms': 162.1128}
W release_lag_ns: {'n': 2663591, 'mean_ms': 0.0384, 'p50_ms': 0.0366, 'p90_ms': 0.0515, 'p99_ms': 0.0712, 'p99.9_ms': 0.0937, 'max_cum_ms': 1.0973}
W release_processing_ns: {'n': 2663591, 'mean_ms': 0.0384, 'p50_ms': 0.0366, 'p90_ms': 0.0515, 'p99_ms': 0.0712, 'p99.9_ms': 0.0937, 'max_cum_ms': 1.0973}
W t_finalize_ns: {'n': 11807, 'mean_ms': 0.2252, 'p50_ms': 0.2181, 'p90_ms': 0.2642, 'p99_ms': 0.3215, 'p99.9_ms': 0.3584, 'max_cum_ms': 0.7897}
W loop_lag_ns: {'n': 53640, 'mean_ms': 0.713, 'p50_ms': 0.1812, 'p90_ms': 1.7449, 'p99_ms': 6.3242, 'p99.9_ms': 8.4541, 'max_cum_ms': 13.5865}
W guard_windows_per_request: {'n': 11989, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_exec_ns: {'n': 12011, 'mean_ms': 3.6216, 'p50_ms': 4.2926, 'p90_ms': 4.5548, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 8.8594}
O guard_batch_windows: {'n': 12011, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_queue_ns: {'n': 12011, 'mean_ms': 0.1159, 'p50_ms': 0.1132, 'p90_ms': 0.1382, 'p99_ms': 0.169, 'p99.9_ms': 0.2181, 'max_cum_ms': 1.3489}
worker counts: {'admitted': 11989, 'audit_enqueued': 23796, 'audit_written': 23795, 'background_round_trips': 10728, 'disposition_ALLOW': 11811, 'disposition_BLOCK': 178, 'guard_windows': 19727, 'lease_granted_tokens{org="org-a"}': 11915520, 'lease_refills': 58, 'provider_calls': 11811, 'provider_connections_opened': 2682, 'quota_admitted_tokens{org="org-a"}': 11383091, 'requests_by_round_trips{n="0"}': 11931, 'requests_by_round_trips{n="1"}': 58, 'shared_state_round_trips': 58}
owner counts: {'guard_batches': 12011, 'guard_windows': 19759, 'owner_requests': 12010, 'owner_windows': 19758}
gauges: {'audit_queue_bound': [181], 'audit_completeness_ratio': [0.9997160704145373, 1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'guard_tokens_per_s': [25729.182604852933, 26396.122167030782], 'input_queue_cap': [2.0], 'guard_queue_cap_tokens': [514.0, 527.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4631, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 231562.64344367638}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4751, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 237565.09950327704}]
notes: []
