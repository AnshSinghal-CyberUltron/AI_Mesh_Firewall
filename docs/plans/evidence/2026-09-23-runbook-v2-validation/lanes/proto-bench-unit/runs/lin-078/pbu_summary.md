# lin-078: strict PASS | load-knee PASS (sut)

checks: {'p99_T_fw_addon_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 23400 (78.0/s) qualified 22975 (76.58/s) FP-blocks 425 (0.01816) expected-blocks 0 infra 0 (0.0) drops 0 safety 0 detection-misses 0
by class: {'benign': {'qualified': 22975, 'policy_block_fp': 425}}
infra reasons: {}
infra detail: {}

T_fw_addon: n=22975 p50=9.7927 p90=12.1251 p99=14.7756 p99.9=19.7648 max=32.8996 mean=9.1809
T_fw_addon_nohold: n=22975 p50=9.6892 p90=11.9345 p99=14.5103 p99.9=15.5119 max=22.6366 mean=9.0369
T_fw_addon_sse: n=16081 p50=9.7972 p90=12.1576 p99=14.8558 p99.9=20.6699 max=32.8996 mean=9.1994
T_fw_addon_json: n=6894 p50=9.7841 p90=12.0679 p99=14.6244 p99.9=15.8745 max=16.6181 mean=9.1379
T_addon_first_sse: n=16081 p50=9.5733 p90=11.7918 p99=14.3714 p99.9=15.313 max=22.5292 mean=8.9123
T_addon_total_sse: n=16081 p50=9.6526 p90=11.865 p99=14.4554 p99.9=15.4215 max=22.6366 mean=8.9937
T_addon_total_json: n=6894 p50=9.7841 p90=12.0679 p99=14.6244 p99.9=15.8745 max=16.6181 mean=9.1379
T_release_lag_max: n=16081 p50=9.7972 p90=12.1576 p99=14.8558 p99.9=20.6699 max=32.8996 mean=9.1994
client_ttft_sse: n=16081 p50=159.6214 p90=161.8421 p99=164.4073 p99.9=165.3621 max=172.6159 mean=158.9597
lateness: n=23400 p50=0.0846 p90=0.0941 p99=0.1055 p99.9=0.132 max=0.3274 mean=0.0846
loadgen: [{'vm': 'rv-pbu-lg-3', 'busy_max': 8.0, 'busy_mean': 4.9, 'late_max_us': 397, 'conn_opens': 223, 'max_inflight': 211}, {'vm': 'rv-pbu-lg-4', 'busy_max': 5.4, 'busy_mean': 5.0, 'late_max_us': 306, 'conn_opens': 223, 'max_inflight': 211}]
wire per client request: {'client_to_gw': 8045.5, 'gw_to_client': 57126.8, 'gw_to_provider': 8833.6, 'provider_to_gw': 57061.0}
gateway cores 2.466 cpu-ms/req 31.716 (workers 27.689, owners 4.021, redis 0.194)
worker util {'n': 18, 'min': 0.088, 'median': 0.123, 'max': 0.139, 'all_sorted': [0.088, 0.098, 0.105, 0.11, 0.112, 0.114, 0.116, 0.118, 0.119, 0.123, 0.125, 0.126, 0.127, 0.129, 0.129, 0.136, 0.139, 0.139]}; per-core schedstat max 0.124 mean 0.107; procstat max 0.264
gpu: {'0': {'samples': 298, 'sm_mean': 11.5, 'sm_max': 26.0, 'mem_mean': 2.1, 'fb_mb_max': 434.0}, '1': {'samples': 298, 'sm_mean': 12.2, 'sm_max': 33.0, 'mem_mean': 2.2, 'fb_mb_max': 434.0}}
rss max total by role (MB): {'redis': 200.1, 'launcher': 59.4, 'owner': 3064.1, 'worker': 3638.9}; fds {'redis': 0, 'launcher': 7, 'owner': 131, 'worker': 1267}
W t_input_ns: {'n': 23386, 'mean_ms': 7.8346, 'p50_ms': 8.5852, 'p90_ms': 10.5513, 'p99_ms': 13.1727, 'p99.9_ms': 13.697, 'max_cum_ms': 17.8401}
W t_admit_ns: {'n': 23385, 'mean_ms': 0.0922, 'p50_ms': 0.0886, 'p90_ms': 0.108, 'p99_ms': 0.1382, 'p99.9_ms': 0.553, 'max_cum_ms': 3.5463}
W t_tokenize_ns: {'n': 23386, 'mean_ms': 3.1548, 'p50_ms': 3.1621, 'p90_ms': 4.7514, 'p99_ms': 5.4723, 'p99.9_ms': 6.1276, 'max_cum_ms': 12.4499}
W t_det_scan_ns: {'n': 23386, 'mean_ms': 0.1067, 'p50_ms': 0.105, 'p90_ms': 0.1382, 'p99_ms': 0.1833, 'p99.9_ms': 0.212, 'max_cum_ms': 0.8241}
W t_guard_wait_ns: {'n': 23386, 'mean_ms': 4.1246, 'p50_ms': 4.7514, 'p90_ms': 5.079, 'p99_ms': 7.1762, 'p99.9_ms': 7.3728, 'max_cum_ms': 8.6669}
W guard_owner_rtt_ns: {'n': 23386, 'mean_ms': 4.32, 'p50_ms': 5.0135, 'p90_ms': 5.3412, 'p99_ms': 7.4383, 'p99.9_ms': 7.7005, 'max_cum_ms': 9.2912}
W guard_queue_ns: {'n': 23386, 'mean_ms': 0.1073, 'p50_ms': 0.105, 'p90_ms': 0.1295, 'p99_ms': 0.1587, 'p99.9_ms': 0.1894, 'max_cum_ms': 2.9457}
W guard_exec_ns: {'n': 23386, 'mean_ms': 3.5725, 'p50_ms': 4.2271, 'p90_ms': 4.4892, 'p99_ms': 6.5208, 'p99.9_ms': 6.7174, 'max_cum_ms': 7.7379}
W dispatch_headers_ns: {'n': 22957, 'mean_ms': 1514.6465, 'p50_ms': 149.9464, 'p90_ms': 5872.0256, 'p99_ms': 7952.4004, 'p99.9_ms': 8153.727, 'max_cum_ms': 8131.722}
W release_lag_ns: {'n': 3617765, 'mean_ms': 0.0504, 'p50_ms': 0.0484, 'p90_ms': 0.066, 'p99_ms': 0.0865, 'p99.9_ms': 0.1091, 'max_cum_ms': 2.1594}
W release_processing_ns: {'n': 3617765, 'mean_ms': 0.0504, 'p50_ms': 0.0484, 'p90_ms': 0.066, 'p99_ms': 0.0865, 'p99.9_ms': 0.1091, 'max_cum_ms': 2.1594}
W t_finalize_ns: {'n': 22952, 'mean_ms': 0.2304, 'p50_ms': 0.2243, 'p90_ms': 0.2765, 'p99_ms': 0.3297, 'p99.9_ms': 0.3748, 'max_cum_ms': 1.4553}
W loop_lag_ns: {'n': 53982, 'mean_ms': 0.0649, 'p50_ms': 0.0, 'p90_ms': 0.1341, 'p99_ms': 1.0199, 'p99.9_ms': 1.5974, 'max_cum_ms': 8.7978}
W guard_windows_per_request: {'n': 23386, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_exec_ns: {'n': 23386, 'mean_ms': 3.5725, 'p50_ms': 4.2271, 'p90_ms': 4.4892, 'p99_ms': 6.5208, 'p99.9_ms': 6.7174, 'max_cum_ms': 7.7379}
O guard_batch_windows: {'n': 23386, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_queue_ns: {'n': 23386, 'mean_ms': 0.1073, 'p50_ms': 0.105, 'p90_ms': 0.1295, 'p99_ms': 0.1587, 'p99.9_ms': 0.1894, 'max_cum_ms': 2.9457}
worker counts: {'admitted': 23385, 'audit_enqueued': 46338, 'audit_written': 46338, 'background_round_trips': 10792, 'disposition_ALLOW': 22961, 'disposition_BLOCK': 425, 'guard_windows': 38514, 'lease_granted_tokens{org="org-a"}': 22187520, 'lease_refills': 108, 'provider_calls': 22961, 'provider_connections_opened': 3542, 'quota_admitted_tokens{org="org-a"}': 22136270, 'requests_by_round_trips{n="0"}': 23277, 'requests_by_round_trips{n="1"}': 108, 'shared_state_round_trips': 108}
owner counts: {'guard_batches': 23386, 'guard_windows': 38514, 'owner_requests': 23386, 'owner_windows': 38514}
gauges: {'audit_queue_bound': [181], 'guard_tokens_per_s': [25911.00543431328, 25957.404722360483], 'audit_completeness_ratio': [1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'input_queue_cap': [68.0], 'guard_queue_cap_tokens': [25911.0, 25957.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 233199, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 233199.04890881953}, {'owner_clients': 9, 'owner_queued_tokens': 1536, 'owner_queue_cap_tokens': 233616, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 233616.64250124435}]
notes: []
