# m86-078: strict FAIL | load-knee FAIL (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': False, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 23400 (78.0/s) qualified 21994 (73.31/s) FP-blocks 320 (0.01368) expected-blocks 0 infra 1086 (0.04641025641025641) drops 0 safety 0 detection-misses 0
by class: {'benign': {'infra_error': 1086, 'qualified': 21994, 'policy_block_fp': 320}}
infra reasons: {'http_503': 1086, 'incomplete': 1086, 'unjoined': 1086, 'disposition_missing': 1086, 'stage_canon_missing': 1086, 'stage_det_missing': 1086, 'stage_sem_missing': 1086, 'stage_resolve_missing': 1086, 'stage_dispatch_missing': 1086, 'stage_out_missing': 1086, 'stage_audit_missing': 1086}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 1086}

T_fw_addon: n=21994 p50=15.0037 p90=22.1259 p99=57.6026 p99.9=75.8776 max=100.4634 mean=16.2418
T_fw_addon_nohold: n=21994 p50=14.7222 p90=17.3248 p99=22.7804 p99.9=27.5908 max=31.2515 mean=13.2395
T_fw_addon_sse: n=15396 p50=15.1039 p90=35.4623 p99=60.996 p99.9=76.2015 max=100.4634 mean=17.5033
T_fw_addon_json: n=6598 p50=14.7847 p90=17.376 p99=22.8462 p99.9=27.5277 max=31.2515 mean=13.2982
T_addon_first_sse: n=15396 p50=14.6475 p90=17.692 p99=34.3605 p99.9=41.4352 max=55.394 mean=13.4803
T_addon_total_sse: n=15396 p50=14.7055 p90=17.3043 p99=22.7221 p99.9=27.5908 max=30.1606 mean=13.2144
T_addon_total_json: n=6598 p50=14.7847 p90=17.376 p99=22.8462 p99.9=27.5277 max=31.2515 mean=13.2982
T_release_lag_max: n=1504 p50=54.3713 p90=61.1064 p99=76.2015 p99.9=89.8344 max=100.4634 mean=52.9874
client_ttft_sse: n=15396 p50=164.695 p90=167.7448 p99=184.4008 p99.9=191.4394 max=205.4883 mean=163.5273
lateness: n=23400 p50=0.0833 p90=0.0932 p99=0.1044 p99.9=0.1284 max=0.5849 mean=0.0834
loadgen: [{'vm': 'rv-pbu-lg-1', 'busy_max': 7.8, 'busy_mean': 4.7, 'late_max_us': 576, 'conn_opens': 213, 'max_inflight': 204}, {'vm': 'rv-pbu-lg-2', 'busy_max': 4.9, 'busy_mean': 4.5, 'late_max_us': 584, 'conn_opens': 214, 'max_inflight': 204}]
wire per client request: {'client_to_gw': 7976.4, 'gw_to_client': 54049.8, 'gw_to_provider': 8481.1, 'provider_to_gw': 54676.3}
gateway cores 2.951 cpu-ms/req 37.964 (workers 30.43, owners 7.528, redis 0.184)
worker util {'n': 18, 'min': 0.077, 'median': 0.131, 'max': 0.187}; per-core schedstat max 0.158 mean 0.127; procstat max 0.258
gpu: {'0': {'samples': 298, 'sm_mean': 24.2, 'sm_max': 63.0, 'mem_mean': 20.7, 'fb_mb_max': 876.0}, '1': {'samples': 298, 'sm_mean': 27.3, 'sm_max': 60.0, 'mem_mean': 23.4, 'fb_mb_max': 876.0}}
rss max total by role (MB): {'redis': 345.9, 'launcher': 59.8, 'owner': 6157.9, 'worker': 5381.4}; fds {'redis': 0, 'launcher': 7, 'owner': 129, 'worker': 1260}
W t_input_ns: {'n': 22306, 'mean_ms': 11.9409, 'p50_ms': 13.566, 'p90_ms': 15.7942, 'p99_ms': 21.1026, 'p99.9_ms': 23.9862, 'max_cum_ms': 28.6231}
W t_admit_ns: {'n': 23389, 'mean_ms': 0.0906, 'p50_ms': 0.0865, 'p90_ms': 0.107, 'p99_ms': 0.1341, 'p99.9_ms': 0.5775, 'max_cum_ms': 2.7857}
W t_tokenize_ns: {'n': 22880, 'mean_ms': 3.3651, 'p50_ms': 3.3915, 'p90_ms': 5.2101, 'p99_ms': 6.0621, 'p99.9_ms': 7.5039, 'max_cum_ms': 9.0423}
W t_det_scan_ns: {'n': 22880, 'mean_ms': 0.1044, 'p50_ms': 0.1019, 'p90_ms': 0.1362, 'p99_ms': 0.1812, 'p99.9_ms': 0.2181, 'max_cum_ms': 1.4474}
W t_guard_wait_ns: {'n': 22881, 'mean_ms': 7.8673, 'p50_ms': 9.6338, 'p90_ms': 10.027, 'p99_ms': 14.4835, 'p99.9_ms': 17.6947, 'max_cum_ms': 23.0612}
W guard_owner_rtt_ns: {'n': 22881, 'mean_ms': 8.0396, 'p50_ms': 9.7649, 'p90_ms': 10.1581, 'p99_ms': 14.7456, 'p99.9_ms': 16.9083, 'max_cum_ms': 21.7181}
W guard_queue_ns: {'n': 22306, 'mean_ms': 0.1134, 'p50_ms': 0.107, 'p90_ms': 0.1321, 'p99_ms': 0.1751, 'p99.9_ms': 1.1878, 'max_cum_ms': 8.5741}
W guard_exec_ns: {'n': 22306, 'mean_ms': 7.4148, 'p50_ms': 8.9784, 'p90_ms': 9.2406, 'p99_ms': 13.697, 'p99.9_ms': 14.3524, 'max_cum_ms': 15.8218}
W dispatch_headers_ns: {'n': 21975, 'mean_ms': 1519.2245, 'p50_ms': 149.9464, 'p90_ms': 5872.0256, 'p99_ms': 7952.4004, 'p99.9_ms': 8153.727, 'max_cum_ms': 8136.4004}
W release_lag_ns: {'n': 3417155, 'mean_ms': 19.8842, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 85.5579}
W holdback_wait_ns: {'n': 3329689, 'mean_ms': 20.3402, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 85.4947}
W release_processing_ns: {'n': 3417155, 'mean_ms': 0.0646, 'p50_ms': 0.0637, 'p90_ms': 0.0845, 'p99_ms': 0.107, 'p99.9_ms': 0.1362, 'max_cum_ms': 1.7721}
W t_finalize_ns: {'n': 21975, 'mean_ms': 0.2544, 'p50_ms': 0.2509, 'p90_ms': 0.3092, 'p99_ms': 0.3748, 'p99.9_ms': 0.4157, 'max_cum_ms': 0.5892}
W loop_lag_ns: {'n': 53630, 'mean_ms': 0.7373, 'p50_ms': 0.0088, 'p90_ms': 2.6051, 'p99_ms': 7.4383, 'p99.9_ms': 10.4202, 'max_cum_ms': 15.376}
W guard_windows_per_request: {'n': 22880, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_exec_ns: {'n': 22322, 'mean_ms': 7.415, 'p50_ms': 8.9784, 'p90_ms': 9.2406, 'p99_ms': 13.697, 'p99.9_ms': 14.3524, 'max_cum_ms': 15.8218}
O guard_batch_windows: {'n': 22322, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_queue_ns: {'n': 22322, 'mean_ms': 0.1134, 'p50_ms': 0.107, 'p90_ms': 0.1321, 'p99_ms': 0.1751, 'p99.9_ms': 1.1878, 'max_cum_ms': 8.5741}
worker counts: {'admitted': 23389, 'audit_enqueued': 44281, 'audit_written': 44281, 'background_round_trips': 10726, 'disposition_ALLOW': 21986, 'disposition_BLOCK': 320, 'guard_owner_sheds': 575, 'guard_windows': 36213, 'lease_granted_tokens{org="org-a"}': 21982080, 'lease_refills': 107, 'provider_calls': 21986, 'provider_connections_opened': 3020, 'quota_admitted_tokens{org="org-a"}': 21962570, 'requests_by_round_trips{n="0"}': 23282, 'requests_by_round_trips{n="1"}': 107, 'shared_state_round_trips': 107, 'shed{reason="guard_owner_queue"}': 575, 'shed{reason="input_queue"}': 509}
owner counts: {'guard_batches': 22322, 'guard_windows': 36240, 'owner_requests': 22898, 'owner_shed': 576, 'owner_windows': 36240}
gauges: {'audit_queue_bound': [181], 'audit_completeness_ratio': [1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'guard_tokens_per_s': [10216.727194476982, 10413.095534492311], 'input_queue_cap': [1.0], 'guard_queue_cap_tokens': [204.0, 208.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 1839, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 91950.54475029284}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 1874, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 93717.8598104308}]
notes: []
