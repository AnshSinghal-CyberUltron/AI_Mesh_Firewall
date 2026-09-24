# p22-078: strict FAIL | load-knee PASS (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 23400 (78.0/s) qualified 22959 (76.53/s) FP-blocks 421 (0.01799) expected-blocks 0 infra 20 (0.0008547008547008547) drops 0 safety 0 detection-misses 0
by class: {'benign': {'qualified': 22959, 'policy_block_fp': 421, 'infra_error': 20}}
infra reasons: {'http_503': 20, 'incomplete': 20, 'unjoined': 20, 'disposition_missing': 20, 'stage_canon_missing': 20, 'stage_det_missing': 20, 'stage_sem_missing': 20, 'stage_resolve_missing': 20, 'stage_dispatch_missing': 20, 'stage_out_missing': 20, 'stage_audit_missing': 20}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 20}

T_fw_addon: n=22959 p50=9.8339 p90=14.4737 p99=52.8792 p99.9=70.523 max=89.4092 mean=12.0377
T_fw_addon_nohold: n=22959 p50=9.6028 p90=12.404 p99=15.7004 p99.9=20.3508 max=28.3323 mean=9.0324
T_fw_addon_sse: n=16072 p50=9.9178 p90=30.9776 p99=53.8681 p99.9=70.8255 max=89.4092 mean=13.2942
T_fw_addon_json: n=6887 p50=9.6336 p90=12.7262 p99=15.7004 p99.9=20.7805 max=23.0659 mean=9.1057
T_addon_first_sse: n=16072 p50=9.5128 p90=12.7968 p99=28.7946 p99.9=33.841 max=50.876 mean=9.2326
T_addon_total_sse: n=16072 p50=9.5873 p90=12.2742 p99=15.7074 p99.9=19.9538 max=28.3323 mean=9.0011
T_addon_total_json: n=6887 p50=9.6336 p90=12.7262 p99=15.7004 p99.9=20.7805 max=23.0659 mean=9.1057
T_release_lag_max: n=1586 p50=49.3736 p90=53.9003 p99=70.9735 p99.9=86.8845 max=89.4092 mean=48.8368
client_ttft_sse: n=16072 p50=159.5579 p90=162.8444 p99=178.82 p99.9=183.963 max=200.9067 mean=159.2807
lateness: n=23400 p50=0.0844 p90=0.0941 p99=0.1055 p99.9=0.1205 max=0.1546 mean=0.0843
loadgen: [{'vm': 'rv-pbu-lg-1', 'busy_max': 17.1, 'busy_mean': 5.0, 'late_max_us': 469, 'conn_opens': 223, 'max_inflight': 211}, {'vm': 'rv-pbu-lg-2', 'busy_max': 17.1, 'busy_mean': 4.8, 'late_max_us': 265, 'conn_opens': 221, 'max_inflight': 210}]
wire per client request: {'client_to_gw': 8153.8, 'gw_to_client': 56361.5, 'gw_to_provider': 8826.8, 'provider_to_gw': 57032.7}
gateway cores 2.748 cpu-ms/req 35.353 (workers 31.301, owners 4.046, redis 0.189)
worker util {'n': 18, 'min': 0.106, 'median': 0.135, 'max': 0.189}; per-core schedstat max 0.136 mean 0.117; procstat max 0.23
gpu: {'0': {'samples': 298, 'sm_mean': 11.2, 'sm_max': 30.0, 'mem_mean': 2.0, 'fb_mb_max': 434.0}, '1': {'samples': 298, 'sm_mean': 12.1, 'sm_max': 28.0, 'mem_mean': 2.2, 'fb_mb_max': 434.0}}
rss max total by role (MB): {'redis': 1052.9, 'launcher': 59.7, 'owner': 3067.0, 'worker': 3658.3}; fds {'redis': 0, 'launcher': 7, 'owner': 126, 'worker': 1286}
W t_input_ns: {'n': 23372, 'mean_ms': 7.7118, 'p50_ms': 8.4541, 'p90_ms': 10.5513, 'p99_ms': 13.1727, 'p99.9_ms': 17.6947, 'max_cum_ms': 28.6342}
W t_admit_ns: {'n': 23392, 'mean_ms': 0.0904, 'p50_ms': 0.0865, 'p90_ms': 0.106, 'p99_ms': 0.1341, 'p99.9_ms': 0.5775, 'max_cum_ms': 3.9185}
W t_tokenize_ns: {'n': 23392, 'mean_ms': 2.9472, 'p50_ms': 2.9655, 'p90_ms': 4.5548, 'p99_ms': 5.2756, 'p99.9_ms': 6.1932, 'max_cum_ms': 8.0838}
W t_det_scan_ns: {'n': 23372, 'mean_ms': 0.1049, 'p50_ms': 0.1019, 'p90_ms': 0.1382, 'p99_ms': 0.1812, 'p99.9_ms': 0.2181, 'max_cum_ms': 3.1584}
W t_guard_wait_ns: {'n': 23372, 'mean_ms': 4.2171, 'p50_ms': 4.8169, 'p90_ms': 5.2101, 'p99_ms': 7.4383, 'p99.9_ms': 12.3863, 'max_cum_ms': 21.4965}
W guard_owner_rtt_ns: {'n': 23372, 'mean_ms': 4.3893, 'p50_ms': 5.0135, 'p90_ms': 5.4067, 'p99_ms': 7.6349, 'p99.9_ms': 11.9931, 'max_cum_ms': 18.8566}
W guard_queue_ns: {'n': 23372, 'mean_ms': 0.1098, 'p50_ms': 0.107, 'p90_ms': 0.1321, 'p99_ms': 0.1628, 'p99.9_ms': 0.8643, 'max_cum_ms': 11.2781}
W guard_exec_ns: {'n': 23372, 'mean_ms': 3.5762, 'p50_ms': 4.2271, 'p90_ms': 4.4892, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 7.9147}
W dispatch_headers_ns: {'n': 22940, 'mean_ms': 1513.6219, 'p50_ms': 149.9464, 'p90_ms': 5872.0256, 'p99_ms': 7952.4004, 'p99.9_ms': 8153.727, 'max_cum_ms': 8139.2743}
W release_lag_ns: {'n': 3570460, 'mean_ms': 19.8892, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 100.0904}
W holdback_wait_ns: {'n': 3479868, 'mean_ms': 20.3412, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 100.0223}
W release_processing_ns: {'n': 3570460, 'mean_ms': 0.0641, 'p50_ms': 0.0632, 'p90_ms': 0.0824, 'p99_ms': 0.107, 'p99.9_ms': 0.1362, 'max_cum_ms': 3.3204}
W t_finalize_ns: {'n': 22945, 'mean_ms': 0.2524, 'p50_ms': 0.2488, 'p90_ms': 0.3052, 'p99_ms': 0.3666, 'p99.9_ms': 0.4035, 'max_cum_ms': 2.7484}
W loop_lag_ns: {'n': 53600, 'mean_ms': 0.7938, 'p50_ms': 0.0089, 'p90_ms': 2.8672, 'p99_ms': 7.9626, 'p99.9_ms': 10.4202, 'max_cum_ms': 56.9536}
W guard_windows_per_request: {'n': 23392, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_exec_ns: {'n': 23334, 'mean_ms': 3.5769, 'p50_ms': 4.2271, 'p90_ms': 4.4892, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 7.9147}
O guard_batch_windows: {'n': 23334, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_queue_ns: {'n': 23334, 'mean_ms': 0.1098, 'p50_ms': 0.107, 'p90_ms': 0.1321, 'p99_ms': 0.1628, 'p99.9_ms': 0.8643, 'max_cum_ms': 11.2781}
worker counts: {'admitted': 23392, 'audit_enqueued': 46317, 'audit_written': 46317, 'background_round_trips': 10720, 'disposition_ALLOW': 22950, 'disposition_BLOCK': 422, 'guard_windows': 38469, 'lease_granted_tokens{org="org-a"}': 21982080, 'lease_refills': 107, 'provider_calls': 22950, 'provider_connections_opened': 3175, 'quota_admitted_tokens{org="org-a"}': 22147831, 'requests_by_round_trips{n="0"}': 23285, 'requests_by_round_trips{n="1"}': 107, 'shared_state_round_trips': 107, 'shed{reason="guard_queue"}': 20}
owner counts: {'guard_batches': 23334, 'guard_windows': 38414, 'owner_requests': 23333, 'owner_windows': 38413}
gauges: {'audit_queue_bound': [181], 'audit_completeness_ratio': [1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'guard_tokens_per_s': [25768.91943081631, 26183.826731337853], 'input_queue_cap': [2.0], 'guard_queue_cap_tokens': [515.0, 523.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4638, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 231920.2748773468}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4713, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 235654.44058204067}]
notes: []
