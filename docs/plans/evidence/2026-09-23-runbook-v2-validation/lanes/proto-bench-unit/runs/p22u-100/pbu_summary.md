# p22u-100: strict FAIL | load-knee PASS (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 30000 (100.0/s) qualified 29507 (98.36/s) FP-blocks 493 (0.01643) expected-blocks 0 infra 0 (0.0) drops 0 safety 0 detection-misses 0
by class: {'benign': {'policy_block_fp': 493, 'qualified': 29507}}
infra reasons: {}
infra detail: {}

T_fw_addon: n=29507 p50=9.9112 p90=15.5489 p99=53.6506 p99.9=71.2748 max=93.3933 mean=12.311
T_fw_addon_nohold: n=29507 p50=9.6389 p90=12.9753 p99=16.1661 p99.9=20.5695 max=28.77 mean=9.225
T_fw_addon_sse: n=20678 p50=10.0135 p90=31.1408 p99=55.0097 p99.9=72.5059 max=93.3933 mean=13.5995
T_fw_addon_json: n=8829 p50=9.6842 p90=13.0794 p99=16.324 p99.9=20.8531 max=27.4589 mean=9.2932
T_addon_first_sse: n=20678 p50=9.5433 p90=13.1922 p99=27.4851 p99.9=33.6466 max=50.5674 mean=9.43
T_addon_total_sse: n=20678 p50=9.6211 p90=12.9174 p99=16.1104 p99.9=20.4016 max=28.77 mean=9.1958
T_addon_total_json: n=8829 p50=9.6842 p90=13.0794 p99=16.324 p99.9=20.8531 max=27.4589 mean=9.2932
T_release_lag_max: n=2076 p50=49.3032 p90=54.9121 p99=72.5059 p99.9=75.2629 max=93.3933 mean=49.0895
client_ttft_sse: n=20678 p50=159.5906 p90=163.237 p99=177.5706 p99.9=183.6688 max=200.6094 mean=159.4738
lateness: n=30000 p50=0.0848 p90=0.0985 p99=0.1155 p99.9=0.1359 max=0.3092 mean=0.0804
loadgen: [{'vm': 'rv-pbu-lg-1', 'busy_max': 8.3, 'busy_mean': 5.3, 'late_max_us': 540, 'conn_opens': 278, 'max_inflight': 259}, {'vm': 'rv-pbu-lg-2', 'busy_max': 5.7, 'busy_mean': 5.1, 'late_max_us': 309, 'conn_opens': 278, 'max_inflight': 259}]
wire per client request: {'client_to_gw': 8108.8, 'gw_to_client': 56688.9, 'gw_to_provider': 8890.5, 'provider_to_gw': 57219.3}
gateway cores 3.486 cpu-ms/req 34.981 (workers 30.943, owners 4.033, redis 0.179)
worker util {'n': 18, 'min': 0.118, 'median': 0.175, 'max': 0.23}; per-core schedstat max 0.177 mean 0.149; procstat max 0.265
gpu: {'0': {'samples': 298, 'sm_mean': 16.5, 'sm_max': 36.0, 'mem_mean': 3.0, 'fb_mb_max': 434.0}, '1': {'samples': 298, 'sm_mean': 14.2, 'sm_max': 34.0, 'mem_mean': 2.6, 'fb_mb_max': 434.0}}
rss max total by role (MB): {'redis': 244.3, 'launcher': 59.8, 'owner': 3063.7, 'worker': 3631.5}; fds {'redis': 0, 'launcher': 7, 'owner': 132, 'worker': 1484}
W t_input_ns: {'n': 29993, 'mean_ms': 7.7532, 'p50_ms': 8.3558, 'p90_ms': 10.6824, 'p99_ms': 14.0902, 'p99.9_ms': 17.4326, 'max_cum_ms': 23.6835}
W t_admit_ns: {'n': 29990, 'mean_ms': 0.0982, 'p50_ms': 0.0937, 'p90_ms': 0.1213, 'p99_ms': 0.1567, 'p99.9_ms': 0.6922, 'max_cum_ms': 2.8697}
W t_tokenize_ns: {'n': 29990, 'mean_ms': 2.886, 'p50_ms': 2.9, 'p90_ms': 4.4237, 'p99_ms': 5.2756, 'p99.9_ms': 6.5208, 'max_cum_ms': 7.8986}
W t_det_scan_ns: {'n': 29990, 'mean_ms': 0.1036, 'p50_ms': 0.0988, 'p90_ms': 0.1382, 'p99_ms': 0.1976, 'p99.9_ms': 0.2386, 'max_cum_ms': 2.1783}
W t_guard_wait_ns: {'n': 29993, 'mean_ms': 4.2812, 'p50_ms': 4.8169, 'p90_ms': 5.3412, 'p99_ms': 8.9784, 'p99.9_ms': 12.6484, 'max_cum_ms': 18.5195}
W guard_owner_rtt_ns: {'n': 29993, 'mean_ms': 4.4424, 'p50_ms': 5.0135, 'p90_ms': 5.5378, 'p99_ms': 8.8474, 'p99.9_ms': 11.7309, 'max_cum_ms': 14.9364}
W guard_queue_ns: {'n': 29993, 'mean_ms': 0.1077, 'p50_ms': 0.1019, 'p90_ms': 0.1295, 'p99_ms': 0.169, 'p99.9_ms': 1.1223, 'max_cum_ms': 5.031}
W guard_exec_ns: {'n': 29993, 'mean_ms': 3.5799, 'p50_ms': 4.2271, 'p90_ms': 4.4892, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 7.3502}
W dispatch_headers_ns: {'n': 29509, 'mean_ms': 1512.5599, 'p50_ms': 149.9464, 'p90_ms': 5872.0256, 'p99_ms': 7952.4004, 'p99.9_ms': 8153.727, 'max_cum_ms': 8132.2869}
W release_lag_ns: {'n': 4605954, 'mean_ms': 19.8891, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 80.132}
W holdback_wait_ns: {'n': 4488815, 'mean_ms': 20.3413, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 80.0831}
W release_processing_ns: {'n': 4605954, 'mean_ms': 0.0651, 'p50_ms': 0.0637, 'p90_ms': 0.0855, 'p99_ms': 0.1132, 'p99.9_ms': 0.1444, 'max_cum_ms': 1.6545}
W t_finalize_ns: {'n': 29515, 'mean_ms': 0.2655, 'p50_ms': 0.2591, 'p90_ms': 0.3338, 'p99_ms': 0.3953, 'p99.9_ms': 0.4362, 'max_cum_ms': 0.5443}
W loop_lag_ns: {'n': 53630, 'mean_ms': 0.7476, 'p50_ms': 0.0106, 'p90_ms': 2.7689, 'p99_ms': 7.6349, 'p99.9_ms': 10.5513, 'max_cum_ms': 27.3658}
W guard_windows_per_request: {'n': 29990, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_exec_ns: {'n': 30012, 'mean_ms': 3.58, 'p50_ms': 4.2271, 'p90_ms': 4.4892, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 7.3502}
O guard_batch_windows: {'n': 30012, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_queue_ns: {'n': 30012, 'mean_ms': 0.1077, 'p50_ms': 0.1019, 'p90_ms': 0.1295, 'p99_ms': 0.169, 'p99.9_ms': 1.1059, 'max_cum_ms': 5.031}
worker counts: {'admitted': 29990, 'audit_enqueued': 59508, 'audit_written': 59509, 'background_round_trips': 10726, 'disposition_ALLOW': 29500, 'disposition_BLOCK': 493, 'guard_windows': 49424, 'lease_granted_tokens{org="org-a"}': 28350720, 'lease_refills': 138, 'provider_calls': 29500, 'provider_connections_opened': 4777, 'quota_admitted_tokens{org="org-a"}': 28424555, 'requests_by_round_trips{n="0"}': 29852, 'requests_by_round_trips{n="1"}': 138, 'shared_state_round_trips': 138}
owner counts: {'guard_batches': 30012, 'guard_windows': 49456, 'owner_requests': 30012, 'owner_windows': 49456}
gauges: {'audit_queue_bound': [181], 'audit_completeness_ratio': [1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'guard_tokens_per_s': [25883.361277508728, 26080.30872146161], 'input_queue_cap': [68.0], 'guard_queue_cap_tokens': [25883.0, 26080.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 232950, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 232950.25149757855}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 234722, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 234722.77849315447}]
notes: []
