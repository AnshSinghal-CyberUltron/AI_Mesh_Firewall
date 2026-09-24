# p22up-150: strict FAIL | load-knee FAIL (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 45542 (151.81/s) qualified 44730 (149.1/s) FP-blocks 810 (0.01779) expected-blocks 0 infra 2 (4.3915506565368234e-05) drops 0 safety 0 detection-misses 0
by class: {'benign': {'qualified': 44730, 'policy_block_fp': 810, 'infra_error': 2}}
infra reasons: {'block_on_unavailable_sem': 2}
infra detail: {'403 http_403 type=policy_violation code=blocked_by_policy': 2}

T_fw_addon: n=44730 p50=11.1144 p90=19.7392 p99=55.8997 p99.9=73.1578 max=92.9558 mean=13.9144
T_fw_addon_nohold: n=44730 p50=10.7612 p90=15.348 p99=21.4502 p99.9=27.6571 max=59.8531 mean=10.7809
T_fw_addon_sse: n=31312 p50=11.2501 p90=34.8797 p99=57.9272 p99.9=74.2259 max=92.9558 mean=15.2151
T_fw_addon_json: n=13418 p50=10.8397 p90=15.4102 p99=21.5708 p99.9=28.1994 max=54.3963 mean=10.8791
T_addon_first_sse: n=31312 p50=10.6519 p90=15.5963 p99=29.7829 p99.9=38.1315 max=66.3766 mean=10.9331
T_addon_total_sse: n=31312 p50=10.7324 p90=15.3164 p99=21.3712 p99.9=27.3996 max=59.8531 mean=10.7388
T_addon_total_json: n=13418 p50=10.8397 p90=15.4102 p99=21.5708 p99.9=28.1994 max=54.3963 mean=10.8791
T_release_lag_max: n=3174 p50=50.6222 p90=57.8582 p99=74.2259 p99.9=90.5804 max=92.9558 mean=50.7895
client_ttft_sse: n=31312 p50=160.6987 p90=165.6427 p99=179.8493 p99.9=188.1335 max=216.4335 mean=160.9761
lateness: n=45542 p50=0.0827 p90=0.0927 p99=0.1041 p99.9=0.1291 max=0.2816 mean=0.082
loadgen: [{'vm': 'rv-pbu-lg-1', 'busy_max': 10.3, 'busy_mean': 6.9, 'late_max_us': 334, 'conn_opens': 434, 'max_inflight': 411}, {'vm': 'rv-pbu-lg-2', 'busy_max': 7.3, 'busy_mean': 6.6, 'late_max_us': 292, 'conn_opens': 429, 'max_inflight': 404}]
wire per client request: {'client_to_gw': 8262.9, 'gw_to_client': 56315.4, 'gw_to_provider': 8985.7, 'provider_to_gw': 56992.3}
gateway cores 5.342 cpu-ms/req 35.304 (workers 31.265, owners 4.036, redis 0.169)
worker util {'n': 18, 'min': 0.198, 'median': 0.272, 'max': 0.305}; per-core schedstat max 0.248 mean 0.227; procstat max 0.354
gpu: {'0': {'samples': 298, 'sm_mean': 23.6, 'sm_max': 57.0, 'mem_mean': 4.3, 'fb_mb_max': 434.0}, '1': {'samples': 298, 'sm_mean': 21.9, 'sm_max': 49.0, 'mem_mean': 4.0, 'fb_mb_max': 434.0}}
rss max total by role (MB): {'redis': 1165.9, 'launcher': 59.8, 'owner': 3064.2, 'worker': 3660.7}; fds {'redis': 0, 'launcher': 7, 'owner': 132, 'worker': 2041}
W t_input_ns: {'n': 45537, 'mean_ms': 9.1024, 'p50_ms': 9.2406, 'p90_ms': 13.4349, 'p99_ms': 18.219, 'p99.9_ms': 23.4619, 'max_cum_ms': 58.2371}
W t_admit_ns: {'n': 45537, 'mean_ms': 0.0949, 'p50_ms': 0.0896, 'p90_ms': 0.1142, 'p99_ms': 0.1485, 'p99.9_ms': 0.6758, 'max_cum_ms': 8.4317}
W t_tokenize_ns: {'n': 45537, 'mean_ms': 3.2852, 'p50_ms': 3.2932, 'p90_ms': 5.1446, 'p99_ms': 6.2587, 'p99.9_ms': 7.1762, 'max_cum_ms': 7.98}
W t_det_scan_ns: {'n': 45537, 'mean_ms': 0.1152, 'p50_ms': 0.1111, 'p90_ms': 0.1567, 'p99_ms': 0.2017, 'p99.9_ms': 0.2427, 'max_cum_ms': 3.4273}
W t_guard_wait_ns: {'n': 45537, 'mean_ms': 5.1833, 'p50_ms': 5.0135, 'p90_ms': 8.0282, 'p99_ms': 13.0417, 'p99.9_ms': 17.9569, 'max_cum_ms': 51.9025}
W guard_owner_rtt_ns: {'n': 45537, 'mean_ms': 5.351, 'p50_ms': 5.2101, 'p90_ms': 8.2248, 'p99_ms': 12.7795, 'p99.9_ms': 17.6947, 'max_cum_ms': 49.3479}
W guard_queue_ns: {'n': 45535, 'mean_ms': 0.8519, 'p50_ms': 0.1193, 'p90_ms': 3.1621, 'p99_ms': 7.4383, 'p99.9_ms': 11.9931, 'max_cum_ms': 18.8432}
W guard_exec_ns: {'n': 45535, 'mean_ms': 3.5954, 'p50_ms': 4.2926, 'p90_ms': 4.5548, 'p99_ms': 6.6519, 'p99.9_ms': 6.8485, 'max_cum_ms': 8.0825}
W dispatch_headers_ns: {'n': 44731, 'mean_ms': 1503.867, 'p50_ms': 149.9464, 'p90_ms': 5872.0256, 'p99_ms': 7952.4004, 'p99.9_ms': 8153.727, 'max_cum_ms': 8141.2944}
W release_lag_ns: {'n': 6960429, 'mean_ms': 19.8877, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.6323, 'max_cum_ms': 100.1049}
W holdback_wait_ns: {'n': 6783183, 'mean_ms': 20.3396, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.6323, 'max_cum_ms': 100.0078}
W release_processing_ns: {'n': 6960429, 'mean_ms': 0.066, 'p50_ms': 0.0648, 'p90_ms': 0.0876, 'p99_ms': 0.1162, 'p99.9_ms': 0.1485, 'max_cum_ms': 35.97}
W t_finalize_ns: {'n': 44700, 'mean_ms': 0.2699, 'p50_ms': 0.2642, 'p90_ms': 0.3379, 'p99_ms': 0.3953, 'p99.9_ms': 0.4403, 'max_cum_ms': 3.0203}
W loop_lag_ns: {'n': 53570, 'mean_ms': 0.8948, 'p50_ms': 0.0118, 'p90_ms': 3.7192, 'p99_ms': 9.5027, 'p99.9_ms': 12.5174, 'max_cum_ms': 44.0351}
W guard_windows_per_request: {'n': 45537, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_exec_ns: {'n': 45557, 'mean_ms': 3.5954, 'p50_ms': 4.2926, 'p90_ms': 4.5548, 'p99_ms': 6.6519, 'p99.9_ms': 6.8485, 'max_cum_ms': 8.0825}
O guard_batch_windows: {'n': 45557, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_queue_ns: {'n': 45557, 'mean_ms': 0.8516, 'p50_ms': 0.1193, 'p90_ms': 3.1621, 'p99_ms': 7.4383, 'p99.9_ms': 11.9931, 'max_cum_ms': 18.8432}
worker counts: {'admitted': 45537, 'audit_enqueued': 90237, 'audit_written': 90236, 'background_round_trips': 10714, 'disposition_ALLOW': 44725, 'disposition_BLOCK': 812, 'guard_deadline_expired': 2, 'guard_unavailable_findings': 2, 'guard_windows': 74779, 'lease_granted_tokens{org="org-a"}': 42526080, 'lease_refills': 207, 'provider_calls': 44725, 'provider_connections_opened': 7525, 'quota_admitted_tokens{org="org-a"}': 43048896, 'requests_by_round_trips{n="0"}': 45330, 'requests_by_round_trips{n="1"}': 207, 'shared_state_round_trips': 207}
owner counts: {'guard_batches': 45557, 'guard_deadline_expired': 2, 'guard_windows': 74816, 'owner_requests': 45559, 'owner_windows': 74819}
gauges: {'audit_queue_bound': [181], 'audit_completeness_ratio': [0.99995194848878, 1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'guard_tokens_per_s': [25883.361277508728, 26080.30872146161], 'input_queue_cap': [68.0], 'guard_queue_cap_tokens': [25883.0, 26080.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 232950, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 232950.25149757855}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 234722, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 234722.77849315447}]
notes: []
