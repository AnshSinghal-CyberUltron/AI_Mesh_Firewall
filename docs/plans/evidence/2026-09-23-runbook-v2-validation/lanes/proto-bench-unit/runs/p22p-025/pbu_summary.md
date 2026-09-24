# p22p-025: strict FAIL | load-knee FAIL (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 7467 (24.89/s) qualified 7290 (24.3/s) FP-blocks 103 (0.01379) expected-blocks 0 infra 74 (0.009910271862863265) drops 0 safety 0 detection-misses 0
by class: {'benign': {'qualified': 7290, 'infra_error': 74, 'policy_block_fp': 103}}
infra reasons: {'http_503': 74, 'incomplete': 74, 'unjoined': 74, 'disposition_missing': 74, 'stage_canon_missing': 74, 'stage_det_missing': 74, 'stage_sem_missing': 74, 'stage_resolve_missing': 74, 'stage_dispatch_missing': 74, 'stage_out_missing': 74, 'stage_audit_missing': 74}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 74}

T_fw_addon: n=7290 p50=9.6902 p90=14.4383 p99=52.8774 p99.9=69.621 max=73.257 mean=11.9712
T_fw_addon_nohold: n=7290 p50=9.4861 p90=12.7164 p99=15.7722 p99.9=19.2626 max=20.6364 mean=8.897
T_fw_addon_sse: n=5093 p50=9.7721 p90=31.1447 p99=53.625 p99.9=69.9491 max=73.257 mean=13.2754
T_fw_addon_json: n=2197 p50=9.5265 p90=12.88 p99=16.2261 p99.9=19.7141 max=20.6364 mean=8.948
T_addon_first_sse: n=5093 p50=9.3934 p90=12.829 p99=28.8185 p99.9=33.7442 max=53.1044 mean=9.1346
T_addon_total_sse: n=5093 p50=9.4633 p90=12.6415 p99=15.6508 p99.9=19.1321 max=19.53 mean=8.8749
T_addon_total_json: n=2197 p50=9.5265 p90=12.88 p99=16.2261 p99.9=19.7141 max=20.6364 mean=8.948
T_release_lag_max: n=511 p50=49.3875 p90=53.6153 p99=69.9491 p99.9=73.257 max=73.257 mean=49.0201
client_ttft_sse: n=5093 p50=159.4474 p90=162.8795 p99=178.852 p99.9=183.8069 max=203.1113 mean=159.1861
lateness: n=7467 p50=0.0836 p90=0.0959 p99=0.1118 p99.9=0.1254 max=0.1623 mean=0.0842
loadgen: [{'vm': 'rv-pbu-lg-1', 'busy_max': 16.2, 'busy_mean': 4.2, 'late_max_us': 363, 'conn_opens': 82, 'max_inflight': 80}, {'vm': 'rv-pbu-lg-2', 'busy_max': 17.0, 'busy_mean': 4.0, 'late_max_us': 200, 'conn_opens': 87, 'max_inflight': 80}]
wire per client request: {'client_to_gw': 9307.1, 'gw_to_client': 56533.0, 'gw_to_provider': 9965.6, 'provider_to_gw': 56986.0}
gateway cores 0.984 cpu-ms/req 39.658 (workers 35.482, owners 4.159, redis 0.288)
worker util {'n': 18, 'min': 0.017, 'median': 0.048, 'max': 0.08}; per-core schedstat max 0.053 mean 0.043; procstat max 0.076
gpu: {'0': {'samples': 298, 'sm_mean': 4.1, 'sm_max': 18.0, 'mem_mean': 0.7, 'fb_mb_max': 434.0}, '1': {'samples': 298, 'sm_mean': 3.7, 'sm_max': 18.0, 'mem_mean': 0.7, 'fb_mb_max': 434.0}}
rss max total by role (MB): {'redis': 1760.8, 'launcher': 59.7, 'owner': 3067.1, 'worker': 3674.4}; fds {'redis': 0, 'launcher': 7, 'owner': 126, 'worker': 764}
W t_input_ns: {'n': 7390, 'mean_ms': 7.6796, 'p50_ms': 8.3558, 'p90_ms': 11.0756, 'p99_ms': 13.3038, 'p99.9_ms': 16.9083, 'max_cum_ms': 31.6019}
W t_admit_ns: {'n': 7464, 'mean_ms': 0.0932, 'p50_ms': 0.0906, 'p90_ms': 0.106, 'p99_ms': 0.1341, 'p99.9_ms': 0.5059, 'max_cum_ms': 3.9185}
W t_tokenize_ns: {'n': 7464, 'mean_ms': 2.7762, 'p50_ms': 2.8344, 'p90_ms': 4.2271, 'p99_ms': 4.948, 'p99.9_ms': 6.2587, 'max_cum_ms': 8.0838}
W t_det_scan_ns: {'n': 7390, 'mean_ms': 0.1013, 'p50_ms': 0.1009, 'p90_ms': 0.1265, 'p99_ms': 0.169, 'p99.9_ms': 0.1997, 'max_cum_ms': 24.0812}
W t_guard_wait_ns: {'n': 7390, 'mean_ms': 4.3612, 'p50_ms': 4.8169, 'p90_ms': 6.783, 'p99_ms': 8.9784, 'p99.9_ms': 12.1242, 'max_cum_ms': 29.4642}
W guard_owner_rtt_ns: {'n': 7390, 'mean_ms': 4.5339, 'p50_ms': 5.0135, 'p90_ms': 6.914, 'p99_ms': 8.8474, 'p99.9_ms': 11.5999, 'max_cum_ms': 28.5289}
W guard_queue_ns: {'n': 7390, 'mean_ms': 0.2, 'p50_ms': 0.1121, 'p90_ms': 0.1382, 'p99_ms': 3.4243, 'p99.9_ms': 5.6689, 'max_cum_ms': 12.2232}
W guard_exec_ns: {'n': 7390, 'mean_ms': 3.6321, 'p50_ms': 4.2271, 'p90_ms': 4.5548, 'p99_ms': 6.5864, 'p99.9_ms': 6.783, 'max_cum_ms': 7.9147}
W dispatch_headers_ns: {'n': 7299, 'mean_ms': 1542.806, 'p50_ms': 149.9464, 'p90_ms': 5939.1345, 'p99_ms': 7885.2915, 'p99.9_ms': 8153.727, 'max_cum_ms': 8139.2743}
W release_lag_ns: {'n': 1142620, 'mean_ms': 19.8876, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 100.1673}
W holdback_wait_ns: {'n': 1113702, 'mean_ms': 20.3371, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 100.0223}
W release_processing_ns: {'n': 1142620, 'mean_ms': 0.0652, 'p50_ms': 0.0653, 'p90_ms': 0.0814, 'p99_ms': 0.1029, 'p99.9_ms': 0.1341, 'max_cum_ms': 26.6219}
W t_finalize_ns: {'n': 7308, 'mean_ms': 0.2445, 'p50_ms': 0.2447, 'p90_ms': 0.2806, 'p99_ms': 0.3461, 'p99.9_ms': 0.383, 'max_cum_ms': 2.7484}
W loop_lag_ns: {'n': 53610, 'mean_ms': 0.7587, 'p50_ms': 0.01, 'p90_ms': 1.3025, 'p99_ms': 7.4383, 'p99.9_ms': 8.7163, 'max_cum_ms': 56.9536}
W guard_windows_per_request: {'n': 7464, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_exec_ns: {'n': 7391, 'mean_ms': 3.6307, 'p50_ms': 4.2271, 'p90_ms': 4.5548, 'p99_ms': 6.5864, 'p99.9_ms': 6.783, 'max_cum_ms': 7.9147}
O guard_batch_windows: {'n': 7391, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_queue_ns: {'n': 7391, 'mean_ms': 0.2, 'p50_ms': 0.1121, 'p90_ms': 0.1382, 'p99_ms': 3.4243, 'p99.9_ms': 5.6689, 'max_cum_ms': 12.2232}
worker counts: {'admitted': 7464, 'audit_enqueued': 14698, 'audit_written': 14698, 'background_round_trips': 10722, 'disposition_ALLOW': 7287, 'disposition_BLOCK': 103, 'guard_windows': 12199, 'lease_granted_tokens{org="org-a"}': 6779520, 'lease_refills': 33, 'provider_calls': 7287, 'provider_connections_opened': 687, 'quota_admitted_tokens{org="org-a"}': 7078940, 'requests_by_round_trips{n="0"}': 7431, 'requests_by_round_trips{n="1"}': 33, 'shared_state_round_trips': 33, 'shed{reason="guard_queue"}': 74}
owner counts: {'guard_batches': 7391, 'guard_windows': 12196, 'owner_requests': 7391, 'owner_windows': 12196}
gauges: {'audit_queue_bound': [181], 'audit_completeness_ratio': [1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'guard_tokens_per_s': [25768.91943081631, 26183.826731337853], 'input_queue_cap': [2.0], 'guard_queue_cap_tokens': [515.0, 523.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4638, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 231920.2748773468}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4713, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 235654.44058204067}]
notes: []
