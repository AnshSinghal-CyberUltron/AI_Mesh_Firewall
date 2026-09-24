# hb-off-10: strict PASS | load-knee PASS (sut)

checks: {'p99_T_fw_addon_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 12000 (40.0/s) qualified 11817 (39.39/s) FP-blocks 183 (0.01525) expected-blocks 0 infra 0 (0.0) drops 0 safety 0 detection-misses 0
by class: {'benign': {'qualified': 11817, 'policy_block_fp': 183}}
infra reasons: {}
infra detail: {}

T_fw_addon: n=11817 p50=12.5786 p90=16.684 p99=19.7263 p99.9=22.1015 max=24.9767 mean=12.8157
T_fw_addon_nohold: n=11817 p50=9.4168 p90=11.9626 p99=14.3821 p99.9=18.608 max=21.8382 mean=8.8807
T_fw_addon_sse: n=11817 p50=12.5786 p90=16.684 p99=19.7263 p99.9=22.1015 max=24.9767 mean=12.8157
T_fw_addon_json: n=0
T_addon_first_sse: n=11817 p50=9.3252 p90=11.8355 p99=14.219 p99.9=17.886 max=20.5295 mean=8.7847
T_addon_total_sse: n=11817 p50=9.4168 p90=11.9626 p99=14.3821 p99.9=18.608 max=21.8382 mean=8.8807
T_addon_total_json: n=0
T_release_lag_max: n=11817 p50=12.5786 p90=16.684 p99=19.7263 p99.9=22.1015 max=24.9767 mean=12.8157
client_ttft_sse: n=11817 p50=159.3773 p90=161.8781 p99=164.2504 p99.9=167.8981 max=170.5508 mean=158.8324
lateness: n=12000 p50=0.0853 p90=0.0955 p99=0.1066 p99.9=0.1257 max=0.1697 mean=0.0857
loadgen: [{'vm': 'rv-pbu-lg-1', 'busy_max': 8.0, 'busy_mean': 4.8, 'late_max_us': 353, 'conn_opens': 63, 'max_inflight': 60}, {'vm': 'rv-pbu-lg-2', 'busy_max': 5.1, 'busy_mean': 4.7, 'late_max_us': 225, 'conn_opens': 63, 'max_inflight': 60}]
wire per client request: {'client_to_gw': 10774.2, 'gw_to_client': 81136.0, 'gw_to_provider': 12698.8, 'provider_to_gw': 81078.5}
gateway cores 1.584 cpu-ms/req 39.736 (workers 35.596, owners 4.129, redis 0.235)
worker util {'n': 18, 'min': 0.032, 'median': 0.083, 'max': 0.117}; per-core schedstat max 0.082 mean 0.069; procstat max 0.166
gpu: {'0': {'samples': 298, 'sm_mean': 5.5, 'sm_max': 18.0, 'mem_mean': 1.0, 'fb_mb_max': 434.0}, '1': {'samples': 298, 'sm_mean': 6.6, 'sm_max': 15.0, 'mem_mean': 1.2, 'fb_mb_max': 434.0}}
rss max total by role (MB): {'redis': 123.0, 'launcher': 59.7, 'owner': 3065.2, 'worker': 3613.8}; fds {'redis': 0, 'launcher': 7, 'owner': 131, 'worker': 683}
W t_input_ns: {'n': 11990, 'mean_ms': 7.5975, 'p50_ms': 8.2903, 'p90_ms': 10.1581, 'p99_ms': 12.7795, 'p99.9_ms': 15.1388, 'max_cum_ms': 18.3601}
W t_admit_ns: {'n': 11990, 'mean_ms': 0.0924, 'p50_ms': 0.0886, 'p90_ms': 0.107, 'p99_ms': 0.1382, 'p99.9_ms': 0.6103, 'max_cum_ms': 3.1107}
W t_tokenize_ns: {'n': 11990, 'mean_ms': 2.8022, 'p50_ms': 2.8344, 'p90_ms': 4.2271, 'p99_ms': 4.8824, 'p99.9_ms': 5.7999, 'max_cum_ms': 7.3276}
W t_det_scan_ns: {'n': 11990, 'mean_ms': 0.113, 'p50_ms': 0.1101, 'p90_ms': 0.1444, 'p99_ms': 0.1935, 'p99.9_ms': 0.2345, 'max_cum_ms': 0.4965}
W t_guard_wait_ns: {'n': 11990, 'mean_ms': 4.237, 'p50_ms': 4.8169, 'p90_ms': 5.2101, 'p99_ms': 7.3073, 'p99.9_ms': 10.6824, 'max_cum_ms': 12.6736}
W guard_owner_rtt_ns: {'n': 11990, 'mean_ms': 4.4221, 'p50_ms': 5.079, 'p90_ms': 5.4723, 'p99_ms': 7.5694, 'p99.9_ms': 9.2406, 'max_cum_ms': 12.2492}
W guard_queue_ns: {'n': 11990, 'mean_ms': 0.1131, 'p50_ms': 0.1101, 'p90_ms': 0.1362, 'p99_ms': 0.1669, 'p99.9_ms': 0.214, 'max_cum_ms': 1.0546}
W guard_exec_ns: {'n': 11990, 'mean_ms': 3.6085, 'p50_ms': 4.2926, 'p90_ms': 4.5548, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 8.8594}
W dispatch_headers_ns: {'n': 11812, 'mean_ms': 150.8415, 'p50_ms': 149.9464, 'p90_ms': 152.0435, 'p99_ms': 152.0435, 'p99.9_ms': 156.2378, 'max_cum_ms': 158.2263}
W release_lag_ns: {'n': 2665285, 'mean_ms': 0.036, 'p50_ms': 0.034, 'p90_ms': 0.0494, 'p99_ms': 0.0691, 'p99.9_ms': 0.0886, 'max_cum_ms': 0.6557}
W release_processing_ns: {'n': 2665285, 'mean_ms': 0.036, 'p50_ms': 0.034, 'p90_ms': 0.0494, 'p99_ms': 0.0691, 'p99.9_ms': 0.0886, 'max_cum_ms': 0.6557}
W t_finalize_ns: {'n': 11825, 'mean_ms': 0.2205, 'p50_ms': 0.214, 'p90_ms': 0.2591, 'p99_ms': 0.3133, 'p99.9_ms': 0.3502, 'max_cum_ms': 0.4091}
W loop_lag_ns: {'n': 53720, 'mean_ms': 0.6189, 'p50_ms': 0.0061, 'p90_ms': 1.5647, 'p99_ms': 6.0621, 'p99.9_ms': 7.8971, 'max_cum_ms': 10.9064}
W guard_windows_per_request: {'n': 11990, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_exec_ns: {'n': 11992, 'mean_ms': 3.6079, 'p50_ms': 4.2926, 'p90_ms': 4.5548, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 8.8594}
O guard_batch_windows: {'n': 11992, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_queue_ns: {'n': 11992, 'mean_ms': 0.1131, 'p50_ms': 0.1101, 'p90_ms': 0.1362, 'p99_ms': 0.1669, 'p99.9_ms': 0.214, 'max_cum_ms': 1.0546}
worker counts: {'admitted': 11990, 'audit_enqueued': 23815, 'audit_written': 23816, 'background_round_trips': 10744, 'disposition_ALLOW': 11807, 'disposition_BLOCK': 183, 'guard_windows': 19733, 'lease_granted_tokens{org="org-a"}': 11093760, 'lease_refills': 54, 'provider_calls': 11807, 'provider_connections_opened': 2787, 'quota_admitted_tokens{org="org-a"}': 11386181, 'requests_by_round_trips{n="0"}': 11936, 'requests_by_round_trips{n="1"}': 54, 'shared_state_round_trips': 54}
owner counts: {'guard_batches': 11992, 'guard_windows': 19733, 'owner_requests': 11992, 'owner_windows': 19733}
gauges: {'audit_queue_bound': [181], 'audit_completeness_ratio': [1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'guard_tokens_per_s': [25729.182604852933, 26396.122167030782], 'input_queue_cap': [2.0], 'guard_queue_cap_tokens': [514.0, 527.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4631, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 231562.64344367638}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4751, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 237565.09950327704}]
notes: []
