# p22-100: strict FAIL | load-knee FAIL (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 30000 (100.0/s) qualified 29278 (97.59/s) FP-blocks 529 (0.01763) expected-blocks 0 infra 193 (0.0064333333333333334) drops 0 safety 0 detection-misses 0
by class: {'benign': {'policy_block_fp': 529, 'qualified': 29278, 'infra_error': 193}}
infra reasons: {'http_503': 193, 'incomplete': 193, 'unjoined': 193, 'disposition_missing': 193, 'stage_canon_missing': 193, 'stage_det_missing': 193, 'stage_sem_missing': 193, 'stage_resolve_missing': 193, 'stage_dispatch_missing': 193, 'stage_out_missing': 193, 'stage_audit_missing': 193}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 193}

T_fw_addon: n=29278 p50=9.8345 p90=15.1018 p99=53.2198 p99.9=70.1053 max=90.7409 mean=12.1325
T_fw_addon_nohold: n=29278 p50=9.5561 p90=12.8511 p99=15.9278 p99.9=20.5651 max=26.3023 mean=9.1551
T_fw_addon_sse: n=20512 p50=9.9304 p90=30.1659 p99=54.075 p99.9=71.3322 max=90.7409 mean=13.3748
T_fw_addon_json: n=8766 p50=9.6168 p90=12.9862 p99=15.8509 p99.9=20.5651 max=26.3023 mean=9.2256
T_addon_first_sse: n=20512 p50=9.4566 p90=13.0424 p99=27.2763 p99.9=33.9578 max=51.9505 mean=9.3566
T_addon_total_sse: n=20512 p50=9.5343 p90=12.7983 p99=15.9566 p99.9=20.7453 max=24.2868 mean=9.125
T_addon_total_json: n=8766 p50=9.6168 p90=12.9862 p99=15.8509 p99.9=20.5651 max=26.3023 mean=9.2256
T_release_lag_max: n=2006 p50=49.3006 p90=54.1683 p99=71.3322 p99.9=89.4875 max=90.7409 mean=48.7828
client_ttft_sse: n=20512 p50=159.5027 p90=163.0936 p99=177.2909 p99.9=183.9878 max=202.0496 mean=159.4006
lateness: n=30000 p50=0.0871 p90=0.099 p99=0.1148 p99.9=0.1363 max=0.2211 mean=0.0864
loadgen: [{'vm': 'rv-pbu-lg-1', 'busy_max': 8.5, 'busy_mean': 5.3, 'late_max_us': 571, 'conn_opens': 277, 'max_inflight': 259}, {'vm': 'rv-pbu-lg-2', 'busy_max': 5.6, 'busy_mean': 5.0, 'late_max_us': 311, 'conn_opens': 269, 'max_inflight': 258}]
wire per client request: {'client_to_gw': 8037.2, 'gw_to_client': 56121.4, 'gw_to_provider': 8783.2, 'provider_to_gw': 56791.5}
gateway cores 3.459 cpu-ms/req 34.701 (workers 30.701, owners 3.996, redis 0.177)
worker util {'n': 18, 'min': 0.143, 'median': 0.173, 'max': 0.199}; per-core schedstat max 0.165 mean 0.147; procstat max 0.261
gpu: {'0': {'samples': 298, 'sm_mean': 15.8, 'sm_max': 37.0, 'mem_mean': 2.9, 'fb_mb_max': 434.0}, '1': {'samples': 298, 'sm_mean': 14.6, 'sm_max': 31.0, 'mem_mean': 2.7, 'fb_mb_max': 434.0}}
rss max total by role (MB): {'redis': 368.2, 'launcher': 59.7, 'owner': 3065.4, 'worker': 3636.4}; fds {'redis': 0, 'launcher': 7, 'owner': 129, 'worker': 1480}
W t_input_ns: {'n': 29792, 'mean_ms': 7.7047, 'p50_ms': 8.2903, 'p90_ms': 10.5513, 'p99_ms': 13.697, 'p99.9_ms': 17.4326, 'max_cum_ms': 21.8371}
W t_admit_ns: {'n': 29984, 'mean_ms': 0.0915, 'p50_ms': 0.0865, 'p90_ms': 0.1091, 'p99_ms': 0.1444, 'p99.9_ms': 0.6758, 'max_cum_ms': 3.0685}
W t_tokenize_ns: {'n': 29984, 'mean_ms': 2.881, 'p50_ms': 2.9, 'p90_ms': 4.3581, 'p99_ms': 5.1446, 'p99.9_ms': 6.3242, 'max_cum_ms': 7.5705}
W t_det_scan_ns: {'n': 29791, 'mean_ms': 0.1027, 'p50_ms': 0.0988, 'p90_ms': 0.1382, 'p99_ms': 0.1935, 'p99.9_ms': 0.2304, 'max_cum_ms': 1.5775}
W t_guard_wait_ns: {'n': 29792, 'mean_ms': 4.2525, 'p50_ms': 4.8169, 'p90_ms': 5.3412, 'p99_ms': 8.5852, 'p99.9_ms': 12.3863, 'max_cum_ms': 15.794}
W guard_owner_rtt_ns: {'n': 29792, 'mean_ms': 4.4149, 'p50_ms': 5.0135, 'p90_ms': 5.5378, 'p99_ms': 8.3558, 'p99.9_ms': 11.5999, 'max_cum_ms': 14.945}
W guard_queue_ns: {'n': 29792, 'mean_ms': 0.1079, 'p50_ms': 0.1019, 'p90_ms': 0.1285, 'p99_ms': 0.1669, 'p99.9_ms': 0.9626, 'max_cum_ms': 4.6329}
W guard_exec_ns: {'n': 29792, 'mean_ms': 3.5709, 'p50_ms': 4.2271, 'p90_ms': 4.4892, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 7.3361}
W dispatch_headers_ns: {'n': 29275, 'mean_ms': 1514.6279, 'p50_ms': 149.9464, 'p90_ms': 5872.0256, 'p99_ms': 7952.4004, 'p99.9_ms': 8153.727, 'max_cum_ms': 8131.8962}
W release_lag_ns: {'n': 4572183, 'mean_ms': 19.8877, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 100.082}
W holdback_wait_ns: {'n': 4455997, 'mean_ms': 20.3395, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 100.0223}
W release_processing_ns: {'n': 4572183, 'mean_ms': 0.065, 'p50_ms': 0.0637, 'p90_ms': 0.0855, 'p99_ms': 0.1121, 'p99.9_ms': 0.1423, 'max_cum_ms': 1.6983}
W t_finalize_ns: {'n': 29287, 'mean_ms': 0.263, 'p50_ms': 0.257, 'p90_ms': 0.3297, 'p99_ms': 0.3871, 'p99.9_ms': 0.4362, 'max_cum_ms': 1.1056}
W loop_lag_ns: {'n': 53640, 'mean_ms': 0.7643, 'p50_ms': 0.0099, 'p90_ms': 2.8017, 'p99_ms': 7.766, 'p99.9_ms': 10.5513, 'max_cum_ms': 39.7339}
W guard_windows_per_request: {'n': 29984, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_exec_ns: {'n': 29743, 'mean_ms': 3.5705, 'p50_ms': 4.2271, 'p90_ms': 4.4892, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 7.3361}
O guard_batch_windows: {'n': 29743, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_queue_ns: {'n': 29743, 'mean_ms': 0.1079, 'p50_ms': 0.1019, 'p90_ms': 0.1285, 'p99_ms': 0.1669, 'p99.9_ms': 0.9626, 'max_cum_ms': 4.6329}
worker counts: {'admitted': 29984, 'audit_enqueued': 59079, 'audit_written': 59079, 'background_round_trips': 10728, 'disposition_ALLOW': 29263, 'disposition_BLOCK': 529, 'guard_windows': 49010, 'lease_granted_tokens{org="org-a"}': 28761600, 'lease_refills': 140, 'provider_calls': 29263, 'provider_connections_opened': 4805, 'quota_admitted_tokens{org="org-a"}': 28400578, 'requests_by_round_trips{n="0"}': 29844, 'requests_by_round_trips{n="1"}': 140, 'shared_state_round_trips': 140, 'shed{reason="guard_queue"}': 193}
owner counts: {'guard_batches': 29743, 'guard_windows': 48924, 'owner_requests': 29743, 'owner_windows': 48924}
gauges: {'audit_queue_bound': [181], 'audit_completeness_ratio': [1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'guard_tokens_per_s': [25768.91943081631, 26183.826731337853], 'input_queue_cap': [2.0], 'guard_queue_cap_tokens': [515.0, 523.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4638, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 231920.2748773468}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4713, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 235654.44058204067}]
notes: []
