# p22p-078: strict FAIL | load-knee FAIL (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 23523 (78.41/s) qualified 22287 (74.29/s) FP-blocks 415 (0.01764) expected-blocks 0 infra 821 (0.03490201079794244) drops 0 safety 0 detection-misses 0
by class: {'benign': {'qualified': 22287, 'infra_error': 821, 'policy_block_fp': 415}}
infra reasons: {'http_503': 821, 'incomplete': 821, 'unjoined': 821, 'disposition_missing': 821, 'stage_canon_missing': 821, 'stage_det_missing': 821, 'stage_sem_missing': 821, 'stage_resolve_missing': 821, 'stage_dispatch_missing': 821, 'stage_out_missing': 821, 'stage_audit_missing': 821}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 821}

T_fw_addon: n=22287 p50=10.101 p90=16.0995 p99=53.5936 p99.9=70.5239 max=89.7511 mean=12.544
T_fw_addon_nohold: n=22287 p50=9.8368 p90=13.4176 p99=17.5444 p99.9=21.868 max=26.0148 mean=9.4761
T_fw_addon_sse: n=15587 p50=10.187 p90=31.8543 p99=55.2564 p99.9=70.765 max=89.7511 mean=13.8276
T_fw_addon_json: n=6700 p50=9.9251 p90=13.5087 p99=17.6344 p99.9=21.067 max=26.0148 mean=9.5579
T_addon_first_sse: n=15587 p50=9.7407 p90=13.4921 p99=28.5237 p99.9=33.9957 max=53.5936 mean=9.6432
T_addon_total_sse: n=15587 p50=9.7996 p90=13.3817 p99=17.4849 p99.9=22.0936 max=25.6122 mean=9.4409
T_addon_total_json: n=6700 p50=9.9251 p90=13.5087 p99=17.6344 p99.9=21.067 max=26.0148 mean=9.5579
T_release_lag_max: n=1580 p50=49.6473 p90=55.0237 p99=70.765 p99.9=78.4693 max=89.7511 mean=49.2761
client_ttft_sse: n=15587 p50=159.7842 p90=163.5435 p99=178.5928 p99.9=184.1105 max=203.6702 mean=159.6902
lateness: n=23523 p50=0.0827 p90=0.0928 p99=0.1038 p99.9=0.1251 max=0.2251 mean=0.0823
loadgen: [{'vm': 'rv-pbu-lg-1', 'busy_max': 8.6, 'busy_mean': 5.2, 'late_max_us': 191, 'conn_opens': 246, 'max_inflight': 219}, {'vm': 'rv-pbu-lg-2', 'busy_max': 5.5, 'busy_mean': 4.9, 'late_max_us': 225, 'conn_opens': 239, 'max_inflight': 223}]
wire per client request: {'client_to_gw': 8303.1, 'gw_to_client': 54418.7, 'gw_to_provider': 8955.0, 'provider_to_gw': 55057.3}
gateway cores 2.688 cpu-ms/req 34.392 (workers 30.466, owners 3.921, redis 0.185)
worker util {'n': 18, 'min': 0.088, 'median': 0.138, 'max': 0.184}; per-core schedstat max 0.126 mean 0.116; procstat max 0.236
gpu: {'0': {'samples': 298, 'sm_mean': 11.4, 'sm_max': 29.0, 'mem_mean': 2.1, 'fb_mb_max': 434.0}, '1': {'samples': 298, 'sm_mean': 11.8, 'sm_max': 35.0, 'mem_mean': 2.1, 'fb_mb_max': 434.0}}
rss max total by role (MB): {'redis': 1657.5, 'launcher': 59.7, 'owner': 3067.0, 'worker': 3671.9}; fds {'redis': 0, 'launcher': 7, 'owner': 129, 'worker': 1295}
W t_input_ns: {'n': 22672, 'mean_ms': 8.129, 'p50_ms': 8.5852, 'p90_ms': 11.9931, 'p99_ms': 15.2699, 'p99.9_ms': 19.2676, 'max_cum_ms': 31.6019}
W t_admit_ns: {'n': 23492, 'mean_ms': 0.0911, 'p50_ms': 0.0865, 'p90_ms': 0.108, 'p99_ms': 0.1382, 'p99.9_ms': 0.5612, 'max_cum_ms': 3.9185}
W t_tokenize_ns: {'n': 23492, 'mean_ms': 2.9804, 'p50_ms': 2.9983, 'p90_ms': 4.6203, 'p99_ms': 5.5378, 'p99.9_ms': 6.783, 'max_cum_ms': 8.0838}
W t_det_scan_ns: {'n': 22673, 'mean_ms': 0.1064, 'p50_ms': 0.1039, 'p90_ms': 0.1403, 'p99_ms': 0.1853, 'p99.9_ms': 0.2284, 'max_cum_ms': 24.0812}
W t_guard_wait_ns: {'n': 22673, 'mean_ms': 4.5731, 'p50_ms': 4.8824, 'p90_ms': 7.0451, 'p99_ms': 10.4202, 'p99.9_ms': 14.0902, 'max_cum_ms': 29.4642}
W guard_owner_rtt_ns: {'n': 22673, 'mean_ms': 4.7471, 'p50_ms': 5.079, 'p90_ms': 7.2417, 'p99_ms': 10.2892, 'p99.9_ms': 13.8281, 'max_cum_ms': 28.5289}
W guard_queue_ns: {'n': 22672, 'mean_ms': 0.3973, 'p50_ms': 0.1111, 'p90_ms': 0.7987, 'p99_ms': 4.6858, 'p99.9_ms': 7.8316, 'max_cum_ms': 12.2232}
W guard_exec_ns: {'n': 22672, 'mean_ms': 3.596, 'p50_ms': 4.2271, 'p90_ms': 4.5548, 'p99_ms': 6.5864, 'p99.9_ms': 6.783, 'max_cum_ms': 7.9147}
W dispatch_headers_ns: {'n': 22263, 'mean_ms': 1519.2357, 'p50_ms': 149.9464, 'p90_ms': 5872.0256, 'p99_ms': 7952.4004, 'p99.9_ms': 8153.727, 'max_cum_ms': 8139.2743}
W release_lag_ns: {'n': 3456374, 'mean_ms': 19.8896, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 100.1673}
W holdback_wait_ns: {'n': 3368286, 'mean_ms': 20.3436, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 100.0223}
W release_processing_ns: {'n': 3456374, 'mean_ms': 0.0645, 'p50_ms': 0.0637, 'p90_ms': 0.0835, 'p99_ms': 0.107, 'p99.9_ms': 0.1362, 'max_cum_ms': 26.6219}
W t_finalize_ns: {'n': 22258, 'mean_ms': 0.2522, 'p50_ms': 0.2488, 'p90_ms': 0.3052, 'p99_ms': 0.3707, 'p99.9_ms': 0.4157, 'max_cum_ms': 2.7484}
W loop_lag_ns: {'n': 53560, 'mean_ms': 0.825, 'p50_ms': 0.0076, 'p90_ms': 3.0638, 'p99_ms': 8.2903, 'p99.9_ms': 10.4202, 'max_cum_ms': 56.9536}
W guard_windows_per_request: {'n': 23492, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_exec_ns: {'n': 22679, 'mean_ms': 3.5961, 'p50_ms': 4.2271, 'p90_ms': 4.5548, 'p99_ms': 6.5864, 'p99.9_ms': 6.783, 'max_cum_ms': 7.9147}
O guard_batch_windows: {'n': 22679, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_queue_ns: {'n': 22679, 'mean_ms': 0.3972, 'p50_ms': 0.1111, 'p90_ms': 0.7987, 'p99_ms': 4.6858, 'p99.9_ms': 7.8316, 'max_cum_ms': 12.2232}
worker counts: {'admitted': 23492, 'audit_enqueued': 44930, 'audit_written': 44929, 'background_round_trips': 10712, 'disposition_ALLOW': 22257, 'disposition_BLOCK': 415, 'guard_owner_sheds': 1, 'guard_windows': 37373, 'lease_granted_tokens{org="org-a"}': 22392960, 'lease_refills': 109, 'provider_calls': 22257, 'provider_connections_opened': 3198, 'quota_admitted_tokens{org="org-a"}': 22247611, 'requests_by_round_trips{n="0"}': 23383, 'requests_by_round_trips{n="1"}': 109, 'shared_state_round_trips': 109, 'shed{reason="guard_owner_queue"}': 1, 'shed{reason="guard_queue"}': 819}
owner counts: {'guard_batches': 22679, 'guard_windows': 37386, 'owner_requests': 22680, 'owner_shed': 1, 'owner_windows': 37386}
gauges: {'audit_queue_bound': [181], 'audit_completeness_ratio': [0.9999685751995475, 1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'guard_tokens_per_s': [25768.91943081631, 26183.826731337853], 'input_queue_cap': [2.0], 'guard_queue_cap_tokens': [515.0, 523.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4638, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 231920.2748773468}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4713, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 235654.44058204067}]
notes: []
