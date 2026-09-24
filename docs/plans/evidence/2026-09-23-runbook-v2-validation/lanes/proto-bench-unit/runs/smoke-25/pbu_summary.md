# smoke-25: strict FAIL | load-knee PASS (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 1500 (25.0/s) qualified 1479 (24.65/s) FP-blocks 21 (0.014) expected-blocks 0 infra 0 (0.0) drops 0 safety 0 detection-misses 0
by class: {'benign': {'qualified': 1479, 'policy_block_fp': 21}}
infra reasons: {}
infra detail: {}

T_fw_addon: n=1479 p50=9.4629 p90=14.2696 p99=50.9849 p99.9=68.9818 max=73.5924 mean=11.8057
T_fw_addon_nohold: n=1479 p50=9.254 p90=11.3415 p99=14.3885 p99.9=21.9658 max=24.5903 mean=8.6153
T_fw_addon_sse: n=1033 p50=9.5055 p90=34.9878 p99=52.7921 p99.9=68.9818 max=73.5924 mean=13.1614
T_fw_addon_json: n=446 p50=9.312 p90=11.7037 p99=13.9325 p99.9=18.4923 max=18.4923 mean=8.6658
T_addon_first_sse: n=1033 p50=9.188 p90=12.5786 p99=26.3661 p99.9=32.9556 max=33.3338 mean=8.8938
T_addon_total_sse: n=1033 p50=9.2365 p90=11.1455 p99=14.8315 p99.9=21.9658 max=24.5903 mean=8.5934
T_addon_total_json: n=446 p50=9.312 p90=11.7037 p99=13.9325 p99.9=18.4923 max=18.4923 mean=8.6658
T_release_lag_max: n=112 p50=48.9989 p90=52.5376 p99=68.9818 p99.9=73.5924 max=73.5924 mean=47.5794
client_ttft_sse: n=1033 p50=159.2292 p90=162.6587 p99=176.4214 p99.9=183.0158 max=183.4104 mean=158.9419
lateness: n=1500 p50=0.0913 p90=0.1072 p99=0.1229 p99.9=0.1452 max=0.1537 mean=0.0919
loadgen: [{'vm': 'rv-pbu-lg-1', 'busy_max': 7.0, 'busy_mean': 4.1, 'late_max_us': 222, 'conn_opens': 68, 'max_inflight': 68}, {'vm': 'rv-pbu-lg-2', 'busy_max': 4.3, 'busy_mean': 4.1, 'late_max_us': 170, 'conn_opens': 68, 'max_inflight': 68}]
wire per client request: {'client_to_gw': 9448.1, 'gw_to_client': 57121.0, 'gw_to_provider': 9924.4, 'provider_to_gw': 57778.6}
gateway cores 0.963 cpu-ms/req 39.161 (workers 34.979, owners 4.165, redis 0.305)
worker util {'n': 18, 'min': 0.032, 'median': 0.05, 'max': 0.071}; per-core schedstat max 0.061 mean 0.042; procstat max 0.068
gpu: {'0': {'samples': 61, 'sm_mean': 3.3, 'sm_max': 14.0, 'mem_mean': 0.6, 'fb_mb_max': 434.0}, '1': {'samples': 61, 'sm_mean': 4.5, 'sm_max': 11.0, 'mem_mean': 0.8, 'fb_mb_max': 434.0}}
rss max total by role (MB): {'redis': 20.2, 'launcher': 59.3, 'owner': 3064.7, 'worker': 3608.0}; fds {'redis': 0, 'launcher': 7, 'owner': 132, 'worker': 714}
W t_input_ns: {'n': 1494, 'mean_ms': 7.3548, 'p50_ms': 8.0937, 'p90_ms': 9.7649, 'p99_ms': 12.5174, 'p99.9_ms': 17.6947, 'max_cum_ms': 21.1873}
W t_admit_ns: {'n': 1493, 'mean_ms': 0.0891, 'p50_ms': 0.0876, 'p90_ms': 0.105, 'p99_ms': 0.1285, 'p99.9_ms': 0.1505, 'max_cum_ms': 2.84}
W t_tokenize_ns: {'n': 1493, 'mean_ms': 2.6138, 'p50_ms': 2.6706, 'p90_ms': 4.0468, 'p99_ms': 4.5548, 'p99.9_ms': 5.2756, 'max_cum_ms': 6.5234}
W t_det_scan_ns: {'n': 1493, 'mean_ms': 0.0973, 'p50_ms': 0.0968, 'p90_ms': 0.1213, 'p99_ms': 0.1464, 'p99.9_ms': 0.1792, 'max_cum_ms': 0.4849}
W t_guard_wait_ns: {'n': 1494, 'mean_ms': 4.2147, 'p50_ms': 4.8169, 'p90_ms': 5.1446, 'p99_ms': 7.4383, 'p99.9_ms': 13.1727, 'max_cum_ms': 13.9344}
W guard_owner_rtt_ns: {'n': 1494, 'mean_ms': 4.3653, 'p50_ms': 5.0135, 'p90_ms': 5.4067, 'p99_ms': 7.5039, 'p99.9_ms': 8.9784, 'max_cum_ms': 12.4322}
W guard_queue_ns: {'n': 1494, 'mean_ms': 0.1115, 'p50_ms': 0.1111, 'p90_ms': 0.1275, 'p99_ms': 0.1526, 'p99.9_ms': 0.1731, 'max_cum_ms': 0.2372}
W guard_exec_ns: {'n': 1494, 'mean_ms': 3.5814, 'p50_ms': 4.2926, 'p90_ms': 4.4892, 'p99_ms': 6.5864, 'p99.9_ms': 7.0451, 'max_cum_ms': 9.0492}
W dispatch_headers_ns: {'n': 1476, 'mean_ms': 1519.9885, 'p50_ms': 149.9464, 'p90_ms': 6140.4611, 'p99_ms': 7818.1827, 'p99.9_ms': 8086.6181, 'max_cum_ms': 8110.6848}
W release_lag_ns: {'n': 232674, 'mean_ms': 19.8987, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 80.0584}
W holdback_wait_ns: {'n': 226834, 'mean_ms': 20.345, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 80.0002}
W release_processing_ns: {'n': 232674, 'mean_ms': 0.0643, 'p50_ms': 0.0637, 'p90_ms': 0.0824, 'p99_ms': 0.1091, 'p99.9_ms': 0.1444, 'max_cum_ms': 0.6632}
W t_finalize_ns: {'n': 1489, 'mean_ms': 0.256, 'p50_ms': 0.2509, 'p90_ms': 0.3092, 'p99_ms': 0.3748, 'p99.9_ms': 0.4403, 'max_cum_ms': 0.4595}
W loop_lag_ns: {'n': 10750, 'mean_ms': 0.6137, 'p50_ms': 0.0288, 'p90_ms': 1.1551, 'p99_ms': 5.8655, 'p99.9_ms': 7.2417, 'max_cum_ms': 10.8931}
W guard_windows_per_request: {'n': 1493, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_exec_ns: {'n': 1497, 'mean_ms': 3.5832, 'p50_ms': 4.2926, 'p90_ms': 4.4892, 'p99_ms': 6.5864, 'p99.9_ms': 7.0451, 'max_cum_ms': 9.0492}
O guard_batch_windows: {'n': 1497, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_queue_ns: {'n': 1497, 'mean_ms': 0.1115, 'p50_ms': 0.1111, 'p90_ms': 0.1275, 'p99_ms': 0.1526, 'p99.9_ms': 0.1731, 'max_cum_ms': 0.2372}
worker counts: {'admitted': 1493, 'audit_enqueued': 2983, 'audit_written': 2982, 'background_round_trips': 2150, 'disposition_ALLOW': 1473, 'disposition_BLOCK': 21, 'guard_windows': 2421, 'provider_calls': 1473, 'provider_connections_opened': 204, 'quota_admitted_tokens{org="org-a"}': 1379539, 'requests_by_round_trips{n="0"}': 1493}
owner counts: {'guard_batches': 1497, 'guard_windows': 2427, 'owner_requests': 1497, 'owner_windows': 2427}
gauges: {'audit_queue_bound': [181], 'audit_completeness_ratio': [0.9935483870967742, 1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'guard_tokens_per_s': [26778.36769188868, 26979.31316245365], 'input_queue_cap': [2.0], 'guard_queue_cap_tokens': [535.0, 539.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4820, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 241005.30922699813}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4856, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 242813.81846208285}]
notes: []
