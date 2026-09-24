# p22-050: strict FAIL | load-knee PASS (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 15000 (50.0/s) qualified 14755 (49.18/s) FP-blocks 245 (0.01633) expected-blocks 0 infra 0 (0.0) drops 0 safety 0 detection-misses 0
by class: {'benign': {'policy_block_fp': 245, 'qualified': 14755}}
infra reasons: {}
infra detail: {}

T_fw_addon: n=14755 p50=9.7924 p90=14.3356 p99=53.1113 p99.9=70.8497 max=76.1724 mean=12.0227
T_fw_addon_nohold: n=14755 p50=9.5158 p90=12.3149 p99=15.1681 p99.9=18.5391 max=22.2373 mean=8.9522
T_fw_addon_sse: n=10327 p50=9.8687 p90=30.4743 p99=54.5525 p99.9=72.0856 max=76.1724 mean=13.3201
T_fw_addon_json: n=4428 p50=9.585 p90=12.3262 p99=14.7438 p99.9=20.4079 max=22.2373 mean=8.9967
T_addon_first_sse: n=10327 p50=9.4164 p90=12.7122 p99=27.0716 p99.9=33.2287 max=45.3365 mean=9.151
T_addon_total_sse: n=10327 p50=9.4954 p90=12.311 p99=15.2174 p99.9=18.3504 max=19.9649 mean=8.9332
T_addon_total_json: n=4428 p50=9.585 p90=12.3262 p99=14.7438 p99.9=20.4079 max=22.2373 mean=8.9967
T_release_lag_max: n=1035 p50=49.2124 p90=54.5525 p99=72.0856 p99.9=75.9346 max=76.1724 mean=49.0638
client_ttft_sse: n=10327 p50=159.4592 p90=162.7557 p99=177.0975 p99.9=183.2838 max=195.3672 mean=159.1954
lateness: n=15000 p50=0.0899 p90=0.1037 p99=0.1193 p99.9=0.1409 max=0.2282 mean=0.0904
loadgen: [{'vm': 'rv-pbu-lg-1', 'busy_max': 14.6, 'busy_mean': 4.6, 'late_max_us': 320, 'conn_opens': 149, 'max_inflight': 137}, {'vm': 'rv-pbu-lg-2', 'busy_max': 17.7, 'busy_mean': 4.5, 'late_max_us': 273, 'conn_opens': 148, 'max_inflight': 136}]
wire per client request: {'client_to_gw': 8354.7, 'gw_to_client': 56370.4, 'gw_to_provider': 9135.0, 'provider_to_gw': 57032.8}
gateway cores 1.777 cpu-ms/req 35.649 (workers 31.58, owners 4.06, redis 0.209)
worker util {'n': 18, 'min': 0.051, 'median': 0.091, 'max': 0.122}; per-core schedstat max 0.095 mean 0.077; procstat max 0.132
gpu: {'0': {'samples': 299, 'sm_mean': 7.7, 'sm_max': 21.0, 'mem_mean': 1.4, 'fb_mb_max': 434.0}, '1': {'samples': 299, 'sm_mean': 7.5, 'sm_max': 20.0, 'mem_mean': 1.4, 'fb_mb_max': 434.0}}
rss max total by role (MB): {'redis': 164.1, 'launcher': 59.7, 'owner': 3065.0, 'worker': 3624.5}; fds {'redis': 0, 'launcher': 7, 'owner': 129, 'worker': 996}
W t_input_ns: {'n': 15013, 'mean_ms': 7.5837, 'p50_ms': 8.2903, 'p90_ms': 10.1581, 'p99_ms': 12.5174, 'p99.9_ms': 15.1388, 'max_cum_ms': 20.2421}
W t_admit_ns: {'n': 15012, 'mean_ms': 0.0962, 'p50_ms': 0.0916, 'p90_ms': 0.1172, 'p99_ms': 0.1526, 'p99.9_ms': 0.6595, 'max_cum_ms': 3.0685}
W t_tokenize_ns: {'n': 15012, 'mean_ms': 2.8219, 'p50_ms': 2.8344, 'p90_ms': 4.2926, 'p99_ms': 4.8169, 'p99.9_ms': 5.9965, 'max_cum_ms': 7.0764}
W t_det_scan_ns: {'n': 15012, 'mean_ms': 0.099, 'p50_ms': 0.0998, 'p90_ms': 0.1213, 'p99_ms': 0.1505, 'p99.9_ms': 0.1853, 'max_cum_ms': 0.7485}
W t_guard_wait_ns: {'n': 15013, 'mean_ms': 4.2, 'p50_ms': 4.7514, 'p90_ms': 5.1446, 'p99_ms': 7.2417, 'p99.9_ms': 10.9445, 'max_cum_ms': 14.3786}
W guard_owner_rtt_ns: {'n': 15013, 'mean_ms': 4.3695, 'p50_ms': 4.948, 'p90_ms': 5.3412, 'p99_ms': 7.4383, 'p99.9_ms': 9.6338, 'max_cum_ms': 13.5549}
W guard_queue_ns: {'n': 15013, 'mean_ms': 0.1088, 'p50_ms': 0.107, 'p90_ms': 0.1265, 'p99_ms': 0.1505, 'p99.9_ms': 0.1833, 'max_cum_ms': 1.4262}
W guard_exec_ns: {'n': 15013, 'mean_ms': 3.5738, 'p50_ms': 4.2271, 'p90_ms': 4.4237, 'p99_ms': 6.4553, 'p99.9_ms': 6.5864, 'max_cum_ms': 7.1697}
W dispatch_headers_ns: {'n': 14773, 'mean_ms': 1519.244, 'p50_ms': 149.9464, 'p90_ms': 5872.0256, 'p99_ms': 7885.2915, 'p99.9_ms': 8153.727, 'max_cum_ms': 8131.8962}
W release_lag_ns: {'n': 2292588, 'mean_ms': 19.8834, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 100.082}
W holdback_wait_ns: {'n': 2233998, 'mean_ms': 20.339, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 100.0223}
W release_processing_ns: {'n': 2292588, 'mean_ms': 0.0642, 'p50_ms': 0.0632, 'p90_ms': 0.0855, 'p99_ms': 0.1132, 'p99.9_ms': 0.1464, 'max_cum_ms': 0.7425}
W t_finalize_ns: {'n': 14789, 'mean_ms': 0.265, 'p50_ms': 0.257, 'p90_ms': 0.3338, 'p99_ms': 0.3912, 'p99.9_ms': 0.4321, 'max_cum_ms': 0.5583}
W loop_lag_ns: {'n': 53700, 'mean_ms': 0.6848, 'p50_ms': 0.0221, 'p90_ms': 1.2698, 'p99_ms': 6.914, 'p99.9_ms': 8.9784, 'max_cum_ms': 39.7339}
W guard_windows_per_request: {'n': 15012, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_exec_ns: {'n': 14984, 'mean_ms': 3.5736, 'p50_ms': 4.2271, 'p90_ms': 4.4237, 'p99_ms': 6.4553, 'p99.9_ms': 6.5864, 'max_cum_ms': 7.1697}
O guard_batch_windows: {'n': 14984, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_queue_ns: {'n': 14984, 'mean_ms': 0.1088, 'p50_ms': 0.107, 'p90_ms': 0.1265, 'p99_ms': 0.1505, 'p99.9_ms': 0.1853, 'max_cum_ms': 1.4262}
worker counts: {'admitted': 15012, 'audit_enqueued': 29802, 'audit_written': 29802, 'background_round_trips': 10740, 'disposition_ALLOW': 14769, 'disposition_BLOCK': 244, 'guard_windows': 24782, 'lease_granted_tokens{org="org-a"}': 14380800, 'lease_refills': 70, 'provider_calls': 14769, 'provider_connections_opened': 2504, 'quota_admitted_tokens{org="org-a"}': 14243922, 'requests_by_round_trips{n="0"}': 14942, 'requests_by_round_trips{n="1"}': 70, 'shared_state_round_trips': 70}
owner counts: {'guard_batches': 14984, 'guard_windows': 24732, 'owner_requests': 14984, 'owner_windows': 24732}
gauges: {'audit_queue_bound': [181], 'audit_completeness_ratio': [1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'guard_tokens_per_s': [25768.91943081631, 26183.826731337853], 'input_queue_cap': [2.0], 'guard_queue_cap_tokens': [515.0, 523.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4638, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 231920.2748773468}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4713, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 235654.44058204067}]
notes: []
