# hb-on-10: strict FAIL | load-knee PASS (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 12000 (40.0/s) qualified 11822 (39.41/s) FP-blocks 178 (0.01483) expected-blocks 0 infra 0 (0.0) drops 0 safety 0 detection-misses 0
by class: {'benign': {'qualified': 11822, 'policy_block_fp': 178}}
infra reasons: {}
infra detail: {}

T_fw_addon: n=11822 p50=29.3125 p90=33.3939 p99=41.2014 p99.9=45.9645 max=53.2198 mean=28.9204
T_fw_addon_nohold: n=11822 p50=9.5244 p90=12.23 p99=14.7745 p99.9=18.3806 max=23.0856 mean=8.9988
T_fw_addon_sse: n=11822 p50=29.3125 p90=33.3939 p99=41.2014 p99.9=45.9645 max=53.2198 mean=28.9204
T_fw_addon_json: n=0
T_addon_first_sse: n=11822 p50=9.4537 p90=12.6831 p99=17.9713 p99.9=23.9212 max=29.6676 mean=9.0377
T_addon_total_sse: n=11822 p50=9.5244 p90=12.23 p99=14.7745 p99.9=18.3806 max=23.0856 mean=8.9988
T_addon_total_json: n=0
T_release_lag_max: n=11822 p50=29.3125 p90=33.3939 p99=41.2014 p99.9=45.9645 max=53.2198 mean=28.9204
client_ttft_sse: n=11822 p50=159.514 p90=162.7305 p99=168.0175 p99.9=173.9908 max=179.7236 mean=159.0864
lateness: n=12000 p50=0.0853 p90=0.0955 p99=0.1084 p99.9=0.123 max=0.2373 mean=0.0852
loadgen: [{'vm': 'rv-pbu-lg-1', 'busy_max': 8.1, 'busy_mean': 4.8, 'late_max_us': 335, 'conn_opens': 63, 'max_inflight': 60}, {'vm': 'rv-pbu-lg-2', 'busy_max': 4.8, 'busy_mean': 4.5, 'late_max_us': 342, 'conn_opens': 63, 'max_inflight': 60}]
wire per client request: {'client_to_gw': 10642.4, 'gw_to_client': 80114.7, 'gw_to_provider': 12689.4, 'provider_to_gw': 81115.3}
gateway cores 1.856 cpu-ms/req 46.555 (workers 42.391, owners 4.153, redis 0.237)
worker util {'n': 18, 'min': 0.049, 'median': 0.098, 'max': 0.123}; per-core schedstat max 0.103 mean 0.081; procstat max 0.187
gpu: {'0': {'samples': 298, 'sm_mean': 5.5, 'sm_max': 19.0, 'mem_mean': 1.0, 'fb_mb_max': 434.0}, '1': {'samples': 298, 'sm_mean': 6.8, 'sm_max': 18.0, 'mem_mean': 1.2, 'fb_mb_max': 434.0}}
rss max total by role (MB): {'redis': 123.2, 'launcher': 59.8, 'owner': 3066.6, 'worker': 3614.0}; fds {'redis': 0, 'launcher': 7, 'owner': 131, 'worker': 684}
W t_input_ns: {'n': 12001, 'mean_ms': 7.6754, 'p50_ms': 8.3558, 'p90_ms': 10.2892, 'p99_ms': 12.9106, 'p99.9_ms': 15.6631, 'max_cum_ms': 19.0412}
W t_admit_ns: {'n': 12000, 'mean_ms': 0.0934, 'p50_ms': 0.0886, 'p90_ms': 0.1101, 'p99_ms': 0.1444, 'p99.9_ms': 0.6103, 'max_cum_ms': 2.9529}
W t_tokenize_ns: {'n': 12000, 'mean_ms': 2.8406, 'p50_ms': 2.8672, 'p90_ms': 4.2926, 'p99_ms': 4.948, 'p99.9_ms': 6.2587, 'max_cum_ms': 7.0683}
W t_det_scan_ns: {'n': 12000, 'mean_ms': 0.103, 'p50_ms': 0.0998, 'p90_ms': 0.1341, 'p99_ms': 0.1833, 'p99.9_ms': 0.2161, 'max_cum_ms': 0.7166}
W t_guard_wait_ns: {'n': 12001, 'mean_ms': 4.2758, 'p50_ms': 4.8824, 'p90_ms': 5.2756, 'p99_ms': 7.4383, 'p99.9_ms': 10.9445, 'max_cum_ms': 12.5901}
W guard_owner_rtt_ns: {'n': 12001, 'mean_ms': 4.4508, 'p50_ms': 5.079, 'p90_ms': 5.5378, 'p99_ms': 7.7005, 'p99.9_ms': 10.2892, 'max_cum_ms': 11.8191}
W guard_queue_ns: {'n': 12001, 'mean_ms': 0.1144, 'p50_ms': 0.1121, 'p90_ms': 0.1362, 'p99_ms': 0.1669, 'p99.9_ms': 0.2017, 'max_cum_ms': 1.0069}
W guard_exec_ns: {'n': 12001, 'mean_ms': 3.6277, 'p50_ms': 4.2926, 'p90_ms': 4.5548, 'p99_ms': 6.6519, 'p99.9_ms': 6.783, 'max_cum_ms': 9.2784}
W dispatch_headers_ns: {'n': 11825, 'mean_ms': 150.8299, 'p50_ms': 149.9464, 'p90_ms': 152.0435, 'p99_ms': 152.0435, 'p99.9_ms': 156.2378, 'max_cum_ms': 158.9106}
W release_lag_ns: {'n': 2632425, 'mean_ms': 9.9728, 'p50_ms': 10.027, 'p90_ms': 10.1581, 'p99_ms': 20.054, 'p99.9_ms': 20.054, 'max_cum_ms': 40.1806}
W holdback_wait_ns: {'n': 2565118, 'mean_ms': 10.1704, 'p50_ms': 10.027, 'p90_ms': 10.027, 'p99_ms': 20.054, 'p99.9_ms': 20.054, 'max_cum_ms': 40.126}
W release_processing_ns: {'n': 2632425, 'mean_ms': 0.0624, 'p50_ms': 0.0617, 'p90_ms': 0.0794, 'p99_ms': 0.1039, 'p99.9_ms': 0.1341, 'max_cum_ms': 1.4524}
W t_finalize_ns: {'n': 11832, 'mean_ms': 0.2636, 'p50_ms': 0.255, 'p90_ms': 0.3092, 'p99_ms': 0.3707, 'p99.9_ms': 0.4239, 'max_cum_ms': 0.473}
W loop_lag_ns: {'n': 53680, 'mean_ms': 0.6553, 'p50_ms': 0.0097, 'p90_ms': 1.5811, 'p99_ms': 6.3898, 'p99.9_ms': 8.8474, 'max_cum_ms': 11.4236}
W guard_windows_per_request: {'n': 12000, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_exec_ns: {'n': 12004, 'mean_ms': 3.6283, 'p50_ms': 4.2926, 'p90_ms': 4.5548, 'p99_ms': 6.6519, 'p99.9_ms': 6.783, 'max_cum_ms': 9.2784}
O guard_batch_windows: {'n': 12004, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_queue_ns: {'n': 12004, 'mean_ms': 0.1144, 'p50_ms': 0.1121, 'p90_ms': 0.1362, 'p99_ms': 0.1669, 'p99.9_ms': 0.2017, 'max_cum_ms': 1.0069}
worker counts: {'admitted': 12000, 'audit_enqueued': 23833, 'audit_written': 23833, 'background_round_trips': 10736, 'disposition_ALLOW': 11823, 'disposition_BLOCK': 178, 'guard_windows': 19744, 'lease_granted_tokens{org="org-a"}': 11299200, 'lease_refills': 55, 'provider_calls': 11823, 'provider_connections_opened': 2557, 'quota_admitted_tokens{org="org-a"}': 11389472, 'requests_by_round_trips{n="0"}': 11945, 'requests_by_round_trips{n="1"}': 55, 'shared_state_round_trips': 55}
owner counts: {'guard_batches': 12004, 'guard_windows': 19752, 'owner_requests': 12003, 'owner_windows': 19751}
gauges: {'audit_queue_bound': [181], 'audit_completeness_ratio': [1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'guard_tokens_per_s': [26013.467373822015, 26032.753479556235], 'input_queue_cap': [2.0], 'guard_queue_cap_tokens': [520.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4682, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 234121.20636439815}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4685, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 234294.78131600612}]
notes: []
