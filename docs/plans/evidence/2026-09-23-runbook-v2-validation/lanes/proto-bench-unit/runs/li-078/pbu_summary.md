# li-078: strict FAIL | load-knee PASS (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 23400 (78.0/s) qualified 22978 (76.59/s) FP-blocks 422 (0.01803) expected-blocks 0 infra 0 (0.0) drops 0 safety 0 detection-misses 0
by class: {'benign': {'qualified': 22978, 'policy_block_fp': 422}}
infra reasons: {}
infra detail: {}

T_fw_addon: n=22978 p50=46.3494 p90=51.9029 p99=70.8396 p99.9=74.848 max=93.9193 mean=36.7707
T_fw_addon_nohold: n=22978 p50=9.7504 p90=12.0384 p99=14.6383 p99.9=15.7469 max=25.0426 mean=9.1064
T_fw_addon_sse: n=16084 p50=49.4388 p90=53.51 p99=71.1647 p99.9=86.051 max=93.9193 mean=48.5959
T_fw_addon_json: n=6894 p50=9.7926 p90=12.1904 p99=14.6992 p99.9=15.6519 max=25.0426 mean=9.182
T_addon_first_sse: n=16084 p50=9.6684 p90=12.2361 p99=28.8476 p99.9=33.1328 max=53.3906 mean=9.2941
T_addon_total_sse: n=16084 p50=9.7379 p90=11.9779 p99=14.614 p99.9=15.7469 max=21.0792 mean=9.0741
T_addon_total_json: n=6894 p50=9.7926 p90=12.1904 p99=14.6992 p99.9=15.6519 max=25.0426 mean=9.182
T_release_lag_max: n=16084 p50=49.4388 p90=53.51 p99=71.1647 p99.9=86.051 max=93.9193 mean=48.5959
client_ttft_sse: n=16084 p50=159.7163 p90=162.2656 p99=178.9121 p99.9=183.181 max=203.4722 mean=159.3422
lateness: n=23400 p50=0.0846 p90=0.0942 p99=0.1058 p99.9=0.1258 max=0.2197 mean=0.0846
loadgen: [{'vm': 'rv-pbu-lg-3', 'busy_max': 15.7, 'busy_mean': 5.0, 'late_max_us': 414, 'conn_opens': 223, 'max_inflight': 211}, {'vm': 'rv-pbu-lg-4', 'busy_max': 17.6, 'busy_mean': 4.9, 'late_max_us': 270, 'conn_opens': 223, 'max_inflight': 211}]
wire per client request: {'client_to_gw': 8087.5, 'gw_to_client': 56651.2, 'gw_to_provider': 8981.7, 'provider_to_gw': 57062.4}
gateway cores 2.655 cpu-ms/req 34.147 (workers 30.099, owners 4.043, redis 0.196)
worker util {'n': 18, 'min': 0.104, 'median': 0.129, 'max': 0.169, 'all_sorted': [0.104, 0.107, 0.112, 0.116, 0.117, 0.118, 0.12, 0.121, 0.122, 0.129, 0.135, 0.136, 0.145, 0.145, 0.146, 0.146, 0.149, 0.169]}; per-core schedstat max 0.137 mean 0.115; procstat max 0.251
gpu: {'0': {'samples': 298, 'sm_mean': 11.7, 'sm_max': 32.0, 'mem_mean': 2.1, 'fb_mb_max': 434.0}, '1': {'samples': 298, 'sm_mean': 11.6, 'sm_max': 27.0, 'mem_mean': 2.1, 'fb_mb_max': 434.0}}
rss max total by role (MB): {'redis': 199.0, 'launcher': 59.7, 'owner': 3065.1, 'worker': 3638.6}; fds {'redis': 0, 'launcher': 7, 'owner': 129, 'worker': 1267}
W t_input_ns: {'n': 23386, 'mean_ms': 7.8787, 'p50_ms': 8.5852, 'p90_ms': 10.5513, 'p99_ms': 13.1727, 'p99.9_ms': 13.8281, 'max_cum_ms': 21.2111}
W t_admit_ns: {'n': 23385, 'mean_ms': 0.0912, 'p50_ms': 0.0876, 'p90_ms': 0.107, 'p99_ms': 0.1362, 'p99.9_ms': 0.5612, 'max_cum_ms': 3.0789}
W t_tokenize_ns: {'n': 23386, 'mean_ms': 3.1755, 'p50_ms': 3.1949, 'p90_ms': 4.7514, 'p99_ms': 5.5378, 'p99.9_ms': 6.3898, 'max_cum_ms': 12.314}
W t_det_scan_ns: {'n': 23386, 'mean_ms': 0.1057, 'p50_ms': 0.1039, 'p90_ms': 0.1382, 'p99_ms': 0.1792, 'p99.9_ms': 0.2161, 'max_cum_ms': 0.4603}
W t_guard_wait_ns: {'n': 23386, 'mean_ms': 4.1503, 'p50_ms': 4.8169, 'p90_ms': 5.1446, 'p99_ms': 7.2417, 'p99.9_ms': 7.4383, 'max_cum_ms': 10.4826}
W guard_owner_rtt_ns: {'n': 23386, 'mean_ms': 4.3451, 'p50_ms': 5.0135, 'p90_ms': 5.4067, 'p99_ms': 7.5039, 'p99.9_ms': 7.7005, 'max_cum_ms': 11.4114}
W guard_queue_ns: {'n': 23386, 'mean_ms': 0.1088, 'p50_ms': 0.107, 'p90_ms': 0.1321, 'p99_ms': 0.1628, 'p99.9_ms': 0.1976, 'max_cum_ms': 4.4954}
W guard_exec_ns: {'n': 23386, 'mean_ms': 3.5888, 'p50_ms': 4.2926, 'p90_ms': 4.4892, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 9.4148}
W dispatch_headers_ns: {'n': 22960, 'mean_ms': 1514.465, 'p50_ms': 149.9464, 'p90_ms': 5872.0256, 'p99_ms': 7952.4004, 'p99.9_ms': 8153.727, 'max_cum_ms': 8131.7042}
W release_lag_ns: {'n': 3572912, 'mean_ms': 19.89, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 80.3475}
W holdback_wait_ns: {'n': 3482335, 'mean_ms': 20.3405, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 80.308}
W release_processing_ns: {'n': 3572912, 'mean_ms': 0.0651, 'p50_ms': 0.0643, 'p90_ms': 0.0835, 'p99_ms': 0.107, 'p99.9_ms': 0.1382, 'max_cum_ms': 2.0161}
W t_finalize_ns: {'n': 22955, 'mean_ms': 0.2534, 'p50_ms': 0.2488, 'p90_ms': 0.3052, 'p99_ms': 0.3707, 'p99.9_ms': 0.4198, 'max_cum_ms': 1.3862}
W loop_lag_ns: {'n': 53982, 'mean_ms': 0.0637, 'p50_ms': 0.0, 'p90_ms': 0.1254, 'p99_ms': 1.0199, 'p99.9_ms': 1.4664, 'max_cum_ms': 5.5499}
W guard_windows_per_request: {'n': 23386, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_exec_ns: {'n': 23386, 'mean_ms': 3.5888, 'p50_ms': 4.2926, 'p90_ms': 4.4892, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 9.4148}
O guard_batch_windows: {'n': 23386, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_queue_ns: {'n': 23386, 'mean_ms': 0.1088, 'p50_ms': 0.107, 'p90_ms': 0.1321, 'p99_ms': 0.1628, 'p99.9_ms': 0.1976, 'max_cum_ms': 4.4954}
worker counts: {'admitted': 23385, 'audit_enqueued': 46341, 'audit_written': 46341, 'background_round_trips': 10794, 'disposition_ALLOW': 22964, 'disposition_BLOCK': 422, 'guard_windows': 38514, 'lease_granted_tokens{org="org-a"}': 21776640, 'lease_refills': 106, 'provider_calls': 22964, 'provider_connections_opened': 3372, 'quota_admitted_tokens{org="org-a"}': 22142161, 'requests_by_round_trips{n="0"}': 23279, 'requests_by_round_trips{n="1"}': 106, 'shared_state_round_trips': 106}
owner counts: {'guard_batches': 23386, 'guard_windows': 38514, 'owner_requests': 23386, 'owner_windows': 38514}
gauges: {'audit_queue_bound': [181], 'guard_tokens_per_s': [25859.570924769152, 26277.94409965762], 'audit_completeness_ratio': [1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'input_queue_cap': [68.0, 69.0], 'guard_queue_cap_tokens': [25859.0, 26277.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 232736, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 232736.13832292237}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 236501, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 236501.49689691857}]
notes: []
