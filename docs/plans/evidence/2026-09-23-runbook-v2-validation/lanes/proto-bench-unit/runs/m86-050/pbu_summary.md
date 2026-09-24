# m86-050: strict FAIL | load-knee FAIL (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': False, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 15000 (50.0/s) qualified 14764 (49.21/s) FP-blocks 197 (0.01313) expected-blocks 0 infra 39 (0.0026) drops 0 safety 0 detection-misses 0
by class: {'benign': {'qualified': 14764, 'policy_block_fp': 197, 'infra_error': 39}}
infra reasons: {'http_503': 39, 'incomplete': 39, 'unjoined': 39, 'disposition_missing': 39, 'stage_canon_missing': 39, 'stage_det_missing': 39, 'stage_sem_missing': 39, 'stage_resolve_missing': 39, 'stage_dispatch_missing': 39, 'stage_out_missing': 39, 'stage_audit_missing': 39}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 39}

T_fw_addon: n=14764 p50=14.6801 p90=21.6509 p99=56.6797 p99.9=75.44 max=95.6726 mean=15.942
T_fw_addon_nohold: n=14764 p50=14.4098 p90=17.0551 p99=22.0221 p99.9=25.7047 max=28.9372 mean=13.0239
T_fw_addon_sse: n=10330 p50=14.78 p90=35.1527 p99=60.6059 p99.9=75.7347 max=95.6726 mean=17.1744
T_fw_addon_json: n=4434 p50=14.4619 p90=17.1293 p99=22.0459 p99.9=24.9787 max=28.9372 mean=13.0709
T_addon_first_sse: n=10330 p50=14.3087 p90=17.4757 p99=28.7952 p99.9=41.2421 max=56.0815 mean=13.1658
T_addon_total_sse: n=10330 p50=14.3893 p90=16.9997 p99=22.006 p99.9=25.7047 max=27.528 mean=13.0038
T_addon_total_json: n=4434 p50=14.4619 p90=17.1293 p99=22.0459 p99.9=24.9787 max=28.9372 mean=13.0709
T_release_lag_max: n=1016 p50=53.9746 p90=60.6545 p99=75.7347 p99.9=80.9847 max=95.6726 mean=52.3532
client_ttft_sse: n=10330 p50=164.3503 p90=167.5517 p99=178.8207 p99.9=191.2517 max=206.1308 mean=163.2099
lateness: n=15000 p50=0.0845 p90=0.0941 p99=0.1069 p99.9=0.1231 max=0.2192 mean=0.0845
loadgen: [{'vm': 'rv-pbu-lg-1', 'busy_max': 7.6, 'busy_mean': 4.5, 'late_max_us': 558, 'conn_opens': 146, 'max_inflight': 137}, {'vm': 'rv-pbu-lg-2', 'busy_max': 4.5, 'busy_mean': 4.3, 'late_max_us': 219, 'conn_opens': 147, 'max_inflight': 137}]
wire per client request: {'client_to_gw': 8285.4, 'gw_to_client': 56412.1, 'gw_to_provider': 9182.8, 'provider_to_gw': 57077.3}
gateway cores 2.004 cpu-ms/req 40.218 (workers 32.204, owners 8.006, redis 0.212)
worker util {'n': 18, 'min': 0.057, 'median': 0.094, 'max': 0.118}; per-core schedstat max 0.12 mean 0.087; procstat max 0.147
gpu: {'0': {'samples': 298, 'sm_mean': 18.2, 'sm_max': 57.0, 'mem_mean': 15.6, 'fb_mb_max': 876.0}, '1': {'samples': 298, 'sm_mean': 17.0, 'sm_max': 47.0, 'mem_mean': 14.6, 'fb_mb_max': 876.0}}
rss max total by role (MB): {'redis': 193.9, 'launcher': 59.8, 'owner': 6157.6, 'worker': 5373.6}; fds {'redis': 0, 'launcher': 7, 'owner': 126, 'worker': 995}
W t_input_ns: {'n': 14949, 'mean_ms': 11.7589, 'p50_ms': 13.1727, 'p90_ms': 15.6631, 'p99_ms': 20.5783, 'p99.9_ms': 22.6755, 'max_cum_ms': 26.5428}
W t_admit_ns: {'n': 14987, 'mean_ms': 0.0879, 'p50_ms': 0.0865, 'p90_ms': 0.0927, 'p99_ms': 0.1121, 'p99.9_ms': 0.51, 'max_cum_ms': 2.7857}
W t_tokenize_ns: {'n': 14952, 'mean_ms': 3.1151, 'p50_ms': 3.031, 'p90_ms': 4.948, 'p99_ms': 5.7999, 'p99.9_ms': 7.3728, 'max_cum_ms': 8.716}
W t_det_scan_ns: {'n': 14952, 'mean_ms': 0.1056, 'p50_ms': 0.0988, 'p90_ms': 0.1505, 'p99_ms': 0.1956, 'p99.9_ms': 0.2345, 'max_cum_ms': 1.4474}
W t_guard_wait_ns: {'n': 14953, 'mean_ms': 8.119, 'p50_ms': 9.6338, 'p90_ms': 9.8959, 'p99_ms': 14.3524, 'p99.9_ms': 15.7942, 'max_cum_ms': 20.6556}
W guard_owner_rtt_ns: {'n': 14953, 'mean_ms': 8.3023, 'p50_ms': 9.7649, 'p90_ms': 10.1581, 'p99_ms': 14.6145, 'p99.9_ms': 15.532, 'max_cum_ms': 20.1767}
W guard_queue_ns: {'n': 14949, 'mean_ms': 0.1138, 'p50_ms': 0.1101, 'p90_ms': 0.1423, 'p99_ms': 0.1792, 'p99.9_ms': 0.2386, 'max_cum_ms': 1.2478}
W guard_exec_ns: {'n': 14949, 'mean_ms': 7.5222, 'p50_ms': 8.9784, 'p90_ms': 9.2406, 'p99_ms': 13.697, 'p99.9_ms': 14.2213, 'max_cum_ms': 15.4537}
W dispatch_headers_ns: {'n': 14749, 'mean_ms': 1519.8664, 'p50_ms': 149.9464, 'p90_ms': 5872.0256, 'p99_ms': 7885.2915, 'p99.9_ms': 8153.727, 'max_cum_ms': 8136.4004}
W release_lag_ns: {'n': 2292447, 'mean_ms': 19.8883, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 81.3919}
W holdback_wait_ns: {'n': 2234340, 'mean_ms': 20.339, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 81.3244}
W release_processing_ns: {'n': 2292447, 'mean_ms': 0.0649, 'p50_ms': 0.0637, 'p90_ms': 0.0865, 'p99_ms': 0.1132, 'p99.9_ms': 0.1464, 'max_cum_ms': 1.7721}
W t_finalize_ns: {'n': 14766, 'mean_ms': 0.2651, 'p50_ms': 0.2591, 'p90_ms': 0.3297, 'p99_ms': 0.3912, 'p99.9_ms': 0.428, 'max_cum_ms': 0.4789}
W loop_lag_ns: {'n': 53650, 'mean_ms': 0.6805, 'p50_ms': 0.0178, 'p90_ms': 1.4991, 'p99_ms': 6.5864, 'p99.9_ms': 9.5027, 'max_cum_ms': 15.376}
W guard_windows_per_request: {'n': 14952, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_exec_ns: {'n': 14953, 'mean_ms': 7.522, 'p50_ms': 8.9784, 'p90_ms': 9.2406, 'p99_ms': 13.697, 'p99.9_ms': 14.2213, 'max_cum_ms': 15.4537}
O guard_batch_windows: {'n': 14953, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_queue_ns: {'n': 14953, 'mean_ms': 0.1138, 'p50_ms': 0.1101, 'p90_ms': 0.1423, 'p99_ms': 0.1792, 'p99.9_ms': 0.2386, 'max_cum_ms': 1.2478}
worker counts: {'admitted': 14987, 'audit_enqueued': 29715, 'audit_written': 29715, 'background_round_trips': 10730, 'disposition_ALLOW': 14754, 'disposition_BLOCK': 195, 'guard_owner_sheds': 4, 'guard_windows': 24666, 'lease_granted_tokens{org="org-a"}': 13559040, 'lease_refills': 66, 'provider_calls': 14754, 'provider_connections_opened': 2376, 'quota_admitted_tokens{org="org-a"}': 14099359, 'requests_by_round_trips{n="0"}': 14921, 'requests_by_round_trips{n="1"}': 66, 'shared_state_round_trips': 66, 'shed{reason="guard_owner_queue"}': 4, 'shed{reason="input_queue"}': 35}
owner counts: {'guard_batches': 14953, 'guard_windows': 24672, 'owner_requests': 14956, 'owner_shed': 4, 'owner_windows': 24671}
gauges: {'audit_queue_bound': [181], 'audit_completeness_ratio': [1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'guard_tokens_per_s': [10216.727194476982, 10413.095534492311], 'input_queue_cap': [1.0], 'guard_queue_cap_tokens': [204.0, 208.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 1839, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 91950.54475029284}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 1874, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 93717.8598104308}]
notes: []
