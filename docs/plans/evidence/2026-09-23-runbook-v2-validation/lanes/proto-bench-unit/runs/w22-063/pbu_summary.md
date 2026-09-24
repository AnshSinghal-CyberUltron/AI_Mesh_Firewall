# w22-063: strict FAIL | load-knee FAIL (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 18900 (63.0/s) qualified 18407 (61.36/s) FP-blocks 483 (0.02556) expected-blocks 0 infra 10 (0.0005291005291005291) drops 0 safety 0 detection-misses 0
by class: {'benign': {'policy_block_fp': 483, 'qualified': 18407, 'infra_error': 10}}
infra reasons: {'http_503': 10, 'incomplete': 10, 'unjoined': 10, 'disposition_missing': 10, 'stage_canon_missing': 10, 'stage_det_missing': 10, 'stage_sem_missing': 10, 'stage_resolve_missing': 10, 'stage_dispatch_missing': 10, 'stage_out_missing': 10, 'stage_audit_missing': 10}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 10}

T_fw_addon: n=18407 p50=14.4466 p90=20.0674 p99=60.1429 p99.9=74.9388 max=94.7085 mean=17.8536
T_fw_addon_nohold: n=18407 p50=14.3224 p90=15.5301 p99=21.4923 p99.9=25.3887 max=30.6234 mean=14.5824
T_fw_addon_sse: n=12886 p50=14.4958 p90=52.8607 p99=72.5143 p99.9=75.1463 max=94.7085 mean=19.2554
T_fw_addon_json: n=5521 p50=14.3592 p90=15.415 p99=20.9537 p99.9=25.3753 max=29.7639 mean=14.5819
T_addon_first_sse: n=12886 p50=14.2367 p90=15.6002 p99=33.7111 p99.9=35.8721 max=54.6885 mean=14.7305
T_addon_total_sse: n=12886 p50=14.3071 p90=15.5689 p99=21.6217 p99.9=25.4934 max=30.6234 mean=14.5827
T_addon_total_json: n=5521 p50=14.3592 p90=15.415 p99=20.9537 p99.9=25.3753 max=29.7639 mean=14.5819
T_release_lag_max: n=1305 p50=54.4076 p90=70.2658 p99=75.1316 p99.9=93.8149 max=94.7085 mean=56.9084
client_ttft_sse: n=12886 p50=164.2851 p90=165.6481 p99=183.7464 p99.9=185.9458 max=204.709 mean=164.7763
lateness: n=18900 p50=0.0856 p90=0.0952 p99=0.1059 p99.9=0.1251 max=0.2457 mean=0.0854
loadgen: [{'vm': 'rv-pbu-lg-1', 'busy_max': 9.2, 'busy_mean': 6.0, 'late_max_us': 450, 'conn_opens': 258, 'max_inflight': 255}, {'vm': 'rv-pbu-lg-2', 'busy_max': 6.0, 'busy_mean': 5.8, 'late_max_us': 245, 'conn_opens': 258, 'max_inflight': 255}]
wire per client request: {'client_to_gw': 13352.2, 'gw_to_client': 98069.7, 'gw_to_provider': 14262.0, 'provider_to_gw': 99436.2}
gateway cores 3.747 cpu-ms/req 59.679 (workers 52.641, owners 7.031, redis 0.216)
worker util {'n': 18, 'min': 0.134, 'median': 0.186, 'max': 0.234}; per-core schedstat max 0.175 mean 0.16; procstat max 0.313
gpu: {'0': {'samples': 298, 'sm_mean': 15.9, 'sm_max': 33.0, 'mem_mean': 2.9, 'fb_mb_max': 434.0}, '1': {'samples': 298, 'sm_mean': 18.3, 'sm_max': 36.0, 'mem_mean': 3.3, 'fb_mb_max': 434.0}}
rss max total by role (MB): {'redis': 2724.0, 'launcher': 59.7, 'owner': 3067.2, 'worker': 3696.6}; fds {'redis': 0, 'launcher': 7, 'owner': 126, 'worker': 1470}
W t_input_ns: {'n': 18872, 'mean_ms': 13.0148, 'p50_ms': 12.9106, 'p90_ms': 13.697, 'p99_ms': 16.3185, 'p99.9_ms': 22.4133, 'max_cum_ms': 32.2244}
W t_admit_ns: {'n': 18880, 'mean_ms': 0.0968, 'p50_ms': 0.0906, 'p90_ms': 0.1121, 'p99_ms': 0.1546, 'p99.9_ms': 0.6349, 'max_cum_ms': 3.9185}
W t_tokenize_ns: {'n': 18880, 'mean_ms': 5.1154, 'p50_ms': 5.1446, 'p90_ms': 5.6689, 'p99_ms': 6.3898, 'p99.9_ms': 7.6349, 'max_cum_ms': 8.7407}
W t_det_scan_ns: {'n': 18870, 'mean_ms': 0.1404, 'p50_ms': 0.1321, 'p90_ms': 0.1792, 'p99_ms': 0.2161, 'p99.9_ms': 0.2447, 'max_cum_ms': 24.0812}
W t_guard_wait_ns: {'n': 18872, 'mean_ms': 7.2234, 'p50_ms': 7.1107, 'p90_ms': 7.3728, 'p99_ms': 10.1581, 'p99.9_ms': 15.9252, 'max_cum_ms': 29.4642}
W guard_owner_rtt_ns: {'n': 18872, 'mean_ms': 7.4759, 'p50_ms': 7.4383, 'p90_ms': 7.6349, 'p99_ms': 9.7649, 'p99.9_ms': 14.8767, 'max_cum_ms': 28.5289}
W guard_queue_ns: {'n': 18872, 'mean_ms': 0.1257, 'p50_ms': 0.1213, 'p90_ms': 0.1505, 'p99_ms': 0.1853, 'p99.9_ms': 1.0281, 'max_cum_ms': 12.2232}
W guard_exec_ns: {'n': 18872, 'mean_ms': 6.497, 'p50_ms': 6.4553, 'p90_ms': 6.6519, 'p99_ms': 6.783, 'p99.9_ms': 7.3073, 'max_cum_ms': 8.4399}
W dispatch_headers_ns: {'n': 18397, 'mean_ms': 2544.9187, 'p50_ms': 149.9464, 'p90_ms': 8153.727, 'p99_ms': 8153.727, 'p99.9_ms': 8153.727, 'max_cum_ms': 8151.0442}
W release_lag_ns: {'n': 5078112, 'mean_ms': 19.9638, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 150.0061}
W holdback_wait_ns: {'n': 4967974, 'mean_ms': 20.3412, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 149.9429}
W release_processing_ns: {'n': 5078112, 'mean_ms': 0.0638, 'p50_ms': 0.0627, 'p90_ms': 0.0835, 'p99_ms': 0.108, 'p99.9_ms': 0.1362, 'max_cum_ms': 26.6219}
W t_finalize_ns: {'n': 18400, 'mean_ms': 0.2658, 'p50_ms': 0.2591, 'p90_ms': 0.3215, 'p99_ms': 0.383, 'p99.9_ms': 0.4198, 'max_cum_ms': 2.7484}
W loop_lag_ns: {'n': 53530, 'mean_ms': 0.8941, 'p50_ms': 0.0075, 'p90_ms': 3.883, 'p99_ms': 9.2406, 'p99.9_ms': 12.5174, 'max_cum_ms': 56.9536}
W guard_windows_per_request: {'n': 18880, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_exec_ns: {'n': 18879, 'mean_ms': 6.497, 'p50_ms': 6.4553, 'p90_ms': 6.6519, 'p99_ms': 6.783, 'p99.9_ms': 7.3073, 'max_cum_ms': 8.4399}
O guard_batch_windows: {'n': 18879, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_queue_ns: {'n': 18879, 'mean_ms': 0.1257, 'p50_ms': 0.1213, 'p90_ms': 0.1505, 'p99_ms': 0.1853, 'p99.9_ms': 1.0281, 'max_cum_ms': 12.2232}
worker counts: {'admitted': 18880, 'audit_enqueued': 37272, 'audit_written': 37272, 'background_round_trips': 10706, 'disposition_ALLOW': 18389, 'disposition_BLOCK': 483, 'guard_windows': 56616, 'lease_granted_tokens{org="org-a"}': 30816000, 'lease_refills': 150, 'provider_calls': 18389, 'provider_connections_opened': 3267, 'quota_admitted_tokens{org="org-a"}': 31693938, 'requests_by_round_trips{n="0"}': 18730, 'requests_by_round_trips{n="1"}': 150, 'shared_state_round_trips': 150, 'shed{reason="guard_queue"}': 10}
owner counts: {'guard_batches': 18879, 'guard_windows': 56637, 'owner_requests': 18879, 'owner_windows': 56637}
gauges: {'audit_queue_bound': [181], 'audit_completeness_ratio': [1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'guard_tokens_per_s': [25768.91943081631, 26183.826731337853], 'input_queue_cap': [2.0], 'guard_queue_cap_tokens': [515.0, 523.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4638, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 231920.2748773468}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4713, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 235654.44058204067}]
notes: []
