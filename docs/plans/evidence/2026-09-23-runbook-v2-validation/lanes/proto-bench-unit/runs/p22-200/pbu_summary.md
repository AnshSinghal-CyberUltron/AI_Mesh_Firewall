# p22-200: strict FAIL | load-knee FAIL (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': False, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 60000 (200.0/s) qualified 56260 (187.53/s) FP-blocks 983 (0.01638) expected-blocks 0 infra 2757 (0.04595) drops 0 safety 0 detection-misses 0
by class: {'benign': {'infra_error': 2757, 'policy_block_fp': 983, 'qualified': 56260}}
infra reasons: {'http_503': 2757, 'incomplete': 2757, 'unjoined': 2757, 'disposition_missing': 2757, 'stage_canon_missing': 2757, 'stage_det_missing': 2757, 'stage_sem_missing': 2757, 'stage_resolve_missing': 2757, 'stage_dispatch_missing': 2757, 'stage_out_missing': 2757, 'stage_audit_missing': 2757}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 2757}

T_fw_addon: n=56260 p50=10.9768 p90=18.6705 p99=55.4404 p99.9=72.2432 max=93.1549 mean=13.6857
T_fw_addon_nohold: n=56260 p50=10.5564 p90=14.7608 p99=20.0285 p99.9=26.0776 max=37.9057 mean=10.5269
T_fw_addon_sse: n=39397 p50=11.1407 p90=33.6015 p99=57.0919 p99.9=73.079 max=93.1549 mean=14.995
T_fw_addon_json: n=16863 p50=10.6356 p90=14.8806 p99=20.5296 p99.9=26.1828 max=34.0405 mean=10.627
T_addon_first_sse: n=39397 p50=10.466 p90=14.9534 p99=30.0248 p99.9=36.8936 max=70.1037 mean=10.7311
T_addon_total_sse: n=39397 p50=10.5193 p90=14.7101 p99=19.856 p99.9=26.0776 max=37.9057 mean=10.484
T_addon_total_json: n=16863 p50=10.6356 p90=14.8806 p99=20.5296 p99.9=26.1828 max=34.0405 mean=10.627
T_release_lag_max: n=3910 p50=50.5247 p90=57.1096 p99=73.079 p99.9=91.0478 max=93.1549 mean=50.8342
client_ttft_sse: n=39397 p50=160.5021 p90=164.9981 p99=180.0508 p99.9=186.9256 max=220.1554 mean=160.7721
lateness: n=60000 p50=0.0837 p90=0.093 p99=0.1044 p99.9=0.1218 max=0.2755 mean=0.0835
loadgen: [{'vm': 'rv-pbu-lg-1', 'busy_max': 10.4, 'busy_mean': 7.4, 'late_max_us': 1090, 'conn_opens': 499, 'max_inflight': 476}, {'vm': 'rv-pbu-lg-2', 'busy_max': 11.1, 'busy_mean': 7.2, 'late_max_us': 223, 'conn_opens': 486, 'max_inflight': 478}]
wire per client request: {'client_to_gw': 7838.8, 'gw_to_client': 53986.2, 'gw_to_provider': 8226.0, 'provider_to_gw': 54529.2}
gateway cores 6.74 cpu-ms/req 33.811 (workers 29.957, owners 3.852, redis 0.158)
worker util {'n': 18, 'min': 0.283, 'median': 0.333, 'max': 0.408}; per-core schedstat max 0.309 mean 0.288; procstat max 0.399
gpu: {'0': {'samples': 298, 'sm_mean': 27.8, 'sm_max': 51.0, 'mem_mean': 5.0, 'fb_mb_max': 434.0}, '1': {'samples': 298, 'sm_mean': 30.1, 'sm_max': 50.0, 'mem_mean': 5.4, 'fb_mb_max': 434.0}}
rss max total by role (MB): {'redis': 761.1, 'launcher': 59.7, 'owner': 3066.0, 'worker': 3652.9}; fds {'redis': 0, 'launcher': 7, 'owner': 129, 'worker': 2349}
W t_input_ns: {'n': 57194, 'mean_ms': 8.7723, 'p50_ms': 9.1095, 'p90_ms': 12.6484, 'p99_ms': 16.5806, 'p99.9_ms': 21.3647, 'max_cum_ms': 28.6342}
W t_admit_ns: {'n': 59947, 'mean_ms': 0.0944, 'p50_ms': 0.0896, 'p90_ms': 0.1142, 'p99_ms': 0.1485, 'p99.9_ms': 0.8069, 'max_cum_ms': 3.9185}
W t_tokenize_ns: {'n': 59947, 'mean_ms': 3.388, 'p50_ms': 3.3915, 'p90_ms': 5.3412, 'p99_ms': 6.3898, 'p99.9_ms': 7.2417, 'max_cum_ms': 8.0838}
W t_det_scan_ns: {'n': 57193, 'mean_ms': 0.1174, 'p50_ms': 0.1091, 'p90_ms': 0.1669, 'p99_ms': 0.2161, 'p99.9_ms': 0.255, 'max_cum_ms': 3.1584}
W t_guard_wait_ns: {'n': 57194, 'mean_ms': 4.727, 'p50_ms': 4.948, 'p90_ms': 7.1762, 'p99_ms': 11.2067, 'p99.9_ms': 15.2699, 'max_cum_ms': 21.4965}
W guard_owner_rtt_ns: {'n': 57194, 'mean_ms': 4.8891, 'p50_ms': 5.2101, 'p90_ms': 7.4383, 'p99_ms': 10.8134, 'p99.9_ms': 14.3524, 'max_cum_ms': 18.8566}
W guard_queue_ns: {'n': 57194, 'mean_ms': 0.3714, 'p50_ms': 0.1121, 'p90_ms': 0.8806, 'p99_ms': 4.6203, 'p99.9_ms': 7.1762, 'max_cum_ms': 11.2781}
W guard_exec_ns: {'n': 57194, 'mean_ms': 3.5904, 'p50_ms': 4.2926, 'p90_ms': 4.5548, 'p99_ms': 6.6519, 'p99.9_ms': 6.8485, 'max_cum_ms': 7.9147}
W dispatch_headers_ns: {'n': 56249, 'mean_ms': 1504.9413, 'p50_ms': 149.9464, 'p90_ms': 5872.0256, 'p99_ms': 7952.4004, 'p99.9_ms': 8153.727, 'max_cum_ms': 8139.2743}
W release_lag_ns: {'n': 8776978, 'mean_ms': 19.8898, 'p50_ms': 20.054, 'p90_ms': 20.3162, 'p99_ms': 40.108, 'p99.9_ms': 41.6809, 'max_cum_ms': 100.082}
W holdback_wait_ns: {'n': 8553946, 'mean_ms': 20.3401, 'p50_ms': 20.054, 'p90_ms': 20.3162, 'p99_ms': 40.108, 'p99.9_ms': 41.6809, 'max_cum_ms': 100.0223}
W release_processing_ns: {'n': 8776978, 'mean_ms': 0.0665, 'p50_ms': 0.0653, 'p90_ms': 0.0896, 'p99_ms': 0.1193, 'p99.9_ms': 0.1526, 'max_cum_ms': 3.3204}
W t_finalize_ns: {'n': 56261, 'mean_ms': 0.2795, 'p50_ms': 0.2724, 'p90_ms': 0.3543, 'p99_ms': 0.4116, 'p99.9_ms': 0.4567, 'max_cum_ms': 2.7484}
W loop_lag_ns: {'n': 53530, 'mean_ms': 0.9107, 'p50_ms': 0.0139, 'p90_ms': 3.8175, 'p99_ms': 9.8959, 'p99.9_ms': 12.3863, 'max_cum_ms': 39.7339}
W guard_windows_per_request: {'n': 59947, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_exec_ns: {'n': 57285, 'mean_ms': 3.5908, 'p50_ms': 4.2926, 'p90_ms': 4.5548, 'p99_ms': 6.6519, 'p99.9_ms': 6.8485, 'max_cum_ms': 7.9147}
O guard_batch_windows: {'n': 57285, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_queue_ns: {'n': 57285, 'mean_ms': 0.3715, 'p50_ms': 0.1121, 'p90_ms': 0.8806, 'p99_ms': 4.6203, 'p99.9_ms': 7.1762, 'max_cum_ms': 11.2781}
worker counts: {'admitted': 59947, 'audit_enqueued': 113455, 'audit_written': 113455, 'background_round_trips': 10706, 'disposition_ALLOW': 56211, 'disposition_BLOCK': 983, 'guard_windows': 93863, 'lease_granted_tokens{org="org-a"}': 57112320, 'lease_refills': 278, 'provider_calls': 56211, 'provider_connections_opened': 9368, 'quota_admitted_tokens{org="org-a"}': 56732940, 'requests_by_round_trips{n="0"}': 59669, 'requests_by_round_trips{n="1"}': 278, 'shared_state_round_trips': 278, 'shed{reason="guard_queue"}': 2754}
owner counts: {'guard_batches': 57285, 'guard_windows': 94023, 'owner_requests': 57284, 'owner_windows': 94021}
gauges: {'audit_queue_bound': [181], 'audit_completeness_ratio': [1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'guard_tokens_per_s': [25768.91943081631, 26183.826731337853], 'input_queue_cap': [2.0], 'guard_queue_cap_tokens': [515.0, 523.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4638, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 231920.2748773468}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4713, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 235654.44058204067}]
notes: []
