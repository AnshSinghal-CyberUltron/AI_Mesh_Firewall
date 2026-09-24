# p22r-100: strict FAIL | load-knee FAIL (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 30000 (100.0/s) qualified 29436 (98.12/s) FP-blocks 515 (0.01717) expected-blocks 0 infra 49 (0.0016333333333333334) drops 0 safety 0 detection-misses 0
by class: {'benign': {'policy_block_fp': 515, 'qualified': 29436, 'infra_error': 49}}
infra reasons: {'http_503': 49, 'incomplete': 49, 'unjoined': 49, 'disposition_missing': 49, 'stage_canon_missing': 49, 'stage_det_missing': 49, 'stage_sem_missing': 49, 'stage_resolve_missing': 49, 'stage_dispatch_missing': 49, 'stage_out_missing': 49, 'stage_audit_missing': 49}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 49}

T_fw_addon: n=29436 p50=9.8906 p90=15.3966 p99=53.4752 p99.9=71.1277 max=86.7375 mean=12.3091
T_fw_addon_nohold: n=29436 p50=9.6069 p90=12.9542 p99=16.2493 p99.9=20.2875 max=27.9529 mean=9.2226
T_fw_addon_sse: n=20625 p50=10.0005 p90=32.2264 p99=54.5951 p99.9=71.6667 max=86.7375 mean=13.5942
T_fw_addon_json: n=8811 p50=9.6896 p90=13.1091 p99=16.2316 p99.9=19.4346 max=23.049 mean=9.3009
T_addon_first_sse: n=20625 p50=9.5034 p90=13.0964 p99=26.8754 p99.9=33.923 max=68.2915 mean=9.3819
T_addon_total_sse: n=20625 p50=9.5771 p90=12.8648 p99=16.264 p99.9=20.6493 max=27.9529 mean=9.1892
T_addon_total_json: n=8811 p50=9.6896 p90=13.1091 p99=16.2316 p99.9=19.4346 max=23.049 mean=9.3009
T_release_lag_max: n=2089 p50=49.463 p90=54.5694 p99=71.6667 p99.9=74.7931 max=86.7375 mean=49.2152
client_ttft_sse: n=20625 p50=159.5499 p90=163.1476 p99=176.9032 p99.9=183.9836 max=218.3229 mean=159.426
lateness: n=30000 p50=0.0864 p90=0.0986 p99=0.1135 p99.9=0.1342 max=0.2885 mean=0.0853
loadgen: [{'vm': 'rv-pbu-lg-1', 'busy_max': 19.0, 'busy_mean': 5.4, 'late_max_us': 529, 'conn_opens': 278, 'max_inflight': 259}, {'vm': 'rv-pbu-lg-2', 'busy_max': 16.6, 'busy_mean': 5.2, 'late_max_us': 288, 'conn_opens': 276, 'max_inflight': 258}]
wire per client request: {'client_to_gw': 8070.6, 'gw_to_client': 56406.2, 'gw_to_provider': 8849.3, 'provider_to_gw': 57081.5}
gateway cores 3.482 cpu-ms/req 34.938 (workers 30.907, owners 4.027, redis 0.179)
worker util {'n': 18, 'min': 0.137, 'median': 0.165, 'max': 0.23}; per-core schedstat max 0.165 mean 0.148; procstat max 0.254
gpu: {'0': {'samples': 298, 'sm_mean': 14.7, 'sm_max': 31.0, 'mem_mean': 2.7, 'fb_mb_max': 434.0}, '1': {'samples': 298, 'sm_mean': 15.8, 'sm_max': 34.0, 'mem_mean': 2.9, 'fb_mb_max': 434.0}}
rss max total by role (MB): {'redis': 244.1, 'launcher': 59.8, 'owner': 3063.8, 'worker': 3630.9}; fds {'redis': 0, 'launcher': 7, 'owner': 132, 'worker': 1479}
W t_input_ns: {'n': 29907, 'mean_ms': 7.7493, 'p50_ms': 8.3558, 'p90_ms': 10.5513, 'p99_ms': 13.566, 'p99.9_ms': 16.9083, 'max_cum_ms': 25.6615}
W t_admit_ns: {'n': 29956, 'mean_ms': 0.0918, 'p50_ms': 0.0865, 'p90_ms': 0.108, 'p99_ms': 0.1444, 'p99.9_ms': 0.6922, 'max_cum_ms': 2.7267}
W t_tokenize_ns: {'n': 29956, 'mean_ms': 2.9057, 'p50_ms': 2.9, 'p90_ms': 4.4237, 'p99_ms': 5.2101, 'p99.9_ms': 6.3898, 'max_cum_ms': 7.7014}
W t_det_scan_ns: {'n': 29907, 'mean_ms': 0.1036, 'p50_ms': 0.0998, 'p90_ms': 0.1382, 'p99_ms': 0.1935, 'p99.9_ms': 0.2406, 'max_cum_ms': 1.7411}
W t_guard_wait_ns: {'n': 29907, 'mean_ms': 4.2673, 'p50_ms': 4.8169, 'p90_ms': 5.3412, 'p99_ms': 8.4541, 'p99.9_ms': 12.1242, 'max_cum_ms': 20.1462}
W guard_owner_rtt_ns: {'n': 29907, 'mean_ms': 4.4301, 'p50_ms': 5.0135, 'p90_ms': 5.5378, 'p99_ms': 8.0282, 'p99.9_ms': 11.2067, 'max_cum_ms': 19.0298}
W guard_queue_ns: {'n': 29907, 'mean_ms': 0.1092, 'p50_ms': 0.1029, 'p90_ms': 0.1306, 'p99_ms': 0.171, 'p99.9_ms': 1.0732, 'max_cum_ms': 4.941}
W guard_exec_ns: {'n': 29907, 'mean_ms': 3.575, 'p50_ms': 4.2271, 'p90_ms': 4.4892, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 7.8359}
W dispatch_headers_ns: {'n': 29414, 'mean_ms': 1514.0831, 'p50_ms': 149.9464, 'p90_ms': 5872.0256, 'p99_ms': 7952.4004, 'p99.9_ms': 8153.727, 'max_cum_ms': 8132.1453}
W release_lag_ns: {'n': 4595502, 'mean_ms': 19.8883, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 82.4754}
W holdback_wait_ns: {'n': 4478663, 'mean_ms': 20.3406, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 82.4124}
W release_processing_ns: {'n': 4595502, 'mean_ms': 0.0648, 'p50_ms': 0.0637, 'p90_ms': 0.0855, 'p99_ms': 0.1121, 'p99.9_ms': 0.1444, 'max_cum_ms': 2.1895}
W t_finalize_ns: {'n': 29431, 'mean_ms': 0.264, 'p50_ms': 0.257, 'p90_ms': 0.3297, 'p99_ms': 0.3912, 'p99.9_ms': 0.4362, 'max_cum_ms': 1.7784}
W loop_lag_ns: {'n': 53640, 'mean_ms': 0.7411, 'p50_ms': 0.0092, 'p90_ms': 2.8017, 'p99_ms': 7.5694, 'p99.9_ms': 10.5513, 'max_cum_ms': 18.7618}
W guard_windows_per_request: {'n': 29956, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_exec_ns: {'n': 29955, 'mean_ms': 3.5751, 'p50_ms': 4.2271, 'p90_ms': 4.4892, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 7.8359}
O guard_batch_windows: {'n': 29955, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_queue_ns: {'n': 29955, 'mean_ms': 0.1092, 'p50_ms': 0.1029, 'p90_ms': 0.1306, 'p99_ms': 0.171, 'p99.9_ms': 1.0732, 'max_cum_ms': 4.941}
worker counts: {'admitted': 29956, 'audit_enqueued': 59338, 'audit_written': 59338, 'background_round_trips': 10728, 'disposition_ALLOW': 29394, 'disposition_BLOCK': 513, 'guard_windows': 49218, 'lease_granted_tokens{org="org-a"}': 28761600, 'lease_refills': 140, 'provider_calls': 29394, 'provider_connections_opened': 4890, 'quota_admitted_tokens{org="org-a"}': 28388567, 'requests_by_round_trips{n="0"}': 29816, 'requests_by_round_trips{n="1"}': 140, 'shared_state_round_trips': 140, 'shed{reason="guard_queue"}': 49}
owner counts: {'guard_batches': 29955, 'guard_windows': 49297, 'owner_requests': 29955, 'owner_windows': 49297}
gauges: {'audit_queue_bound': [181], 'audit_completeness_ratio': [1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'guard_tokens_per_s': [26050.27758631763, 26605.448685725496], 'input_queue_cap': [7.0], 'guard_queue_cap_tokens': [2605.0, 2660.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 23445, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 234452.49827685865}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 23944, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 239449.03817152948}]
notes: []
