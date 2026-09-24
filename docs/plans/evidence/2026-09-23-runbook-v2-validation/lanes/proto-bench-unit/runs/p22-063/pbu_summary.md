# p22-063: strict FAIL | load-knee PASS (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 18900 (63.0/s) qualified 18580 (61.93/s) FP-blocks 318 (0.01683) expected-blocks 0 infra 2 (0.00010582010582010582) drops 0 safety 0 detection-misses 0
by class: {'benign': {'qualified': 18580, 'policy_block_fp': 318, 'infra_error': 2}}
infra reasons: {'http_503': 2, 'incomplete': 2, 'unjoined': 2, 'disposition_missing': 2, 'stage_canon_missing': 2, 'stage_det_missing': 2, 'stage_sem_missing': 2, 'stage_resolve_missing': 2, 'stage_dispatch_missing': 2, 'stage_out_missing': 2, 'stage_audit_missing': 2}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 2}

T_fw_addon: n=18580 p50=9.757 p90=14.3461 p99=52.2109 p99.9=70.4133 max=86.5895 mean=11.9335
T_fw_addon_nohold: n=18580 p50=9.509 p90=12.3159 p99=15.2284 p99.9=20.4206 max=32.0209 mean=8.9456
T_fw_addon_sse: n=13000 p50=9.8354 p90=29.8179 p99=53.4871 p99.9=71.2693 max=86.5895 mean=13.1688
T_fw_addon_json: n=5580 p50=9.5977 p90=12.7667 p99=15.5194 p99.9=21.2232 max=32.0209 mean=9.0557
T_addon_first_sse: n=13000 p50=9.413 p90=12.9693 p99=26.8511 p99.9=33.9063 max=53.2305 mean=9.1672
T_addon_total_sse: n=13000 p50=9.4736 p90=12.183 p99=15.077 p99.9=19.9096 max=23.5603 mean=8.8983
T_addon_total_json: n=5580 p50=9.5977 p90=12.7667 p99=15.5194 p99.9=21.2232 max=32.0209 mean=9.0557
T_release_lag_max: n=1262 p50=49.2378 p90=53.6504 p99=71.4377 p99.9=85.0786 max=86.5895 mean=48.7884
client_ttft_sse: n=13000 p50=159.4584 p90=163.0115 p99=176.8778 p99.9=183.9854 max=203.3139 mean=159.2154
lateness: n=18900 p50=0.0844 p90=0.0941 p99=0.106 p99.9=0.123 max=0.236 mean=0.0842
loadgen: [{'vm': 'rv-pbu-lg-1', 'busy_max': 8.0, 'busy_mean': 4.7, 'late_max_us': 493, 'conn_opens': 179, 'max_inflight': 172}, {'vm': 'rv-pbu-lg-2', 'busy_max': 5.9, 'busy_mean': 4.5, 'late_max_us': 281, 'conn_opens': 179, 'max_inflight': 172}]
wire per client request: {'client_to_gw': 8256.4, 'gw_to_client': 56317.3, 'gw_to_provider': 8868.0, 'provider_to_gw': 56985.1}
gateway cores 2.239 cpu-ms/req 35.658 (workers 31.578, owners 4.073, redis 0.199)
worker util {'n': 18, 'min': 0.07, 'median': 0.111, 'max': 0.163}; per-core schedstat max 0.128 mean 0.098; procstat max 0.211
gpu: {'0': {'samples': 298, 'sm_mean': 8.6, 'sm_max': 24.0, 'mem_mean': 1.6, 'fb_mb_max': 434.0}, '1': {'samples': 298, 'sm_mean': 10.3, 'sm_max': 23.0, 'mem_mean': 1.9, 'fb_mb_max': 434.0}}
rss max total by role (MB): {'redis': 892.4, 'launcher': 59.7, 'owner': 3066.0, 'worker': 3655.9}; fds {'redis': 0, 'launcher': 7, 'owner': 126, 'worker': 1133}
W t_input_ns: {'n': 18886, 'mean_ms': 7.6611, 'p50_ms': 8.3558, 'p90_ms': 10.4202, 'p99_ms': 13.0417, 'p99.9_ms': 17.4326, 'max_cum_ms': 28.6342}
W t_admit_ns: {'n': 18890, 'mean_ms': 0.0904, 'p50_ms': 0.0865, 'p90_ms': 0.105, 'p99_ms': 0.1321, 'p99.9_ms': 0.5448, 'max_cum_ms': 3.9185}
W t_tokenize_ns: {'n': 18890, 'mean_ms': 2.8981, 'p50_ms': 2.9327, 'p90_ms': 4.4237, 'p99_ms': 5.2101, 'p99.9_ms': 6.5208, 'max_cum_ms': 8.0838}
W t_det_scan_ns: {'n': 18888, 'mean_ms': 0.1034, 'p50_ms': 0.1009, 'p90_ms': 0.1341, 'p99_ms': 0.1792, 'p99.9_ms': 0.212, 'max_cum_ms': 3.1584}
W t_guard_wait_ns: {'n': 18886, 'mean_ms': 4.2204, 'p50_ms': 4.8169, 'p90_ms': 5.2101, 'p99_ms': 7.3728, 'p99.9_ms': 12.2552, 'max_cum_ms': 21.4965}
W guard_owner_rtt_ns: {'n': 18886, 'mean_ms': 4.3929, 'p50_ms': 5.0135, 'p90_ms': 5.4723, 'p99_ms': 7.5694, 'p99.9_ms': 11.7309, 'max_cum_ms': 18.8566}
W guard_queue_ns: {'n': 18886, 'mean_ms': 0.1102, 'p50_ms': 0.107, 'p90_ms': 0.1321, 'p99_ms': 0.1628, 'p99.9_ms': 0.9462, 'max_cum_ms': 11.2781}
W guard_exec_ns: {'n': 18886, 'mean_ms': 3.5877, 'p50_ms': 4.2271, 'p90_ms': 4.4892, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 7.9147}
W dispatch_headers_ns: {'n': 18579, 'mean_ms': 1518.3165, 'p50_ms': 149.9464, 'p90_ms': 5872.0256, 'p99_ms': 7885.2915, 'p99.9_ms': 8153.727, 'max_cum_ms': 8139.2743}
W release_lag_ns: {'n': 2881169, 'mean_ms': 19.886, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 100.0904}
W holdback_wait_ns: {'n': 2807824, 'mean_ms': 20.3394, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 100.0223}
W release_processing_ns: {'n': 2881169, 'mean_ms': 0.0644, 'p50_ms': 0.0637, 'p90_ms': 0.0824, 'p99_ms': 0.105, 'p99.9_ms': 0.1341, 'max_cum_ms': 3.3204}
W t_finalize_ns: {'n': 18613, 'mean_ms': 0.2494, 'p50_ms': 0.2468, 'p90_ms': 0.297, 'p99_ms': 0.3625, 'p99.9_ms': 0.4157, 'max_cum_ms': 2.7484}
W loop_lag_ns: {'n': 53610, 'mean_ms': 0.7739, 'p50_ms': 0.0073, 'p90_ms': 2.6051, 'p99_ms': 7.7005, 'p99.9_ms': 10.027, 'max_cum_ms': 56.9536}
W guard_windows_per_request: {'n': 18890, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_exec_ns: {'n': 18883, 'mean_ms': 3.5879, 'p50_ms': 4.2271, 'p90_ms': 4.4892, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 7.9147}
O guard_batch_windows: {'n': 18883, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_queue_ns: {'n': 18883, 'mean_ms': 0.1102, 'p50_ms': 0.107, 'p90_ms': 0.1321, 'p99_ms': 0.1628, 'p99.9_ms': 0.9462, 'max_cum_ms': 11.2781}
worker counts: {'admitted': 18890, 'audit_enqueued': 37499, 'audit_written': 37499, 'background_round_trips': 10722, 'disposition_ALLOW': 18568, 'disposition_BLOCK': 318, 'guard_windows': 31134, 'lease_granted_tokens{org="org-a"}': 18284160, 'lease_refills': 89, 'provider_calls': 18568, 'provider_connections_opened': 2428, 'quota_admitted_tokens{org="org-a"}': 17887512, 'requests_by_round_trips{n="0"}': 18801, 'requests_by_round_trips{n="1"}': 89, 'shared_state_round_trips': 89, 'shed{reason="guard_queue"}': 2}
owner counts: {'guard_batches': 18883, 'guard_windows': 31131, 'owner_requests': 18883, 'owner_windows': 31131}
gauges: {'audit_queue_bound': [181], 'audit_completeness_ratio': [1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'guard_tokens_per_s': [25768.91943081631, 26183.826731337853], 'input_queue_cap': [2.0], 'guard_queue_cap_tokens': [515.0, 523.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4638, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 231920.2748773468}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4713, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 235654.44058204067}]
notes: []
