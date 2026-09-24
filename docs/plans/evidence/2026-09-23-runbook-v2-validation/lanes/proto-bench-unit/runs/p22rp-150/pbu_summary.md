# p22rp-150: strict FAIL | load-knee FAIL (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': False, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 45542 (151.81/s) qualified 44661 (148.87/s) FP-blocks 799 (0.01754) expected-blocks 0 infra 82 (0.0018005357691800975) drops 0 safety 0 detection-misses 0
by class: {'benign': {'qualified': 44661, 'policy_block_fp': 799, 'infra_error': 82}}
infra reasons: {'http_503': 82, 'incomplete': 82, 'unjoined': 82, 'disposition_missing': 82, 'stage_canon_missing': 82, 'stage_det_missing': 82, 'stage_sem_missing': 82, 'stage_resolve_missing': 82, 'stage_dispatch_missing': 82, 'stage_out_missing': 82, 'stage_audit_missing': 82}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 82}

T_fw_addon: n=44661 p50=11.1028 p90=19.9165 p99=56.0642 p99.9=72.9445 max=92.6822 mean=13.9441
T_fw_addon_nohold: n=44661 p50=10.7467 p90=15.302 p99=21.3516 p99.9=27.4598 max=37.8881 mean=10.7607
T_fw_addon_sse: n=31273 p50=11.2525 p90=35.443 p99=58.4065 p99.9=74.1378 max=92.6822 mean=15.2795
T_fw_addon_json: n=13388 p50=10.8112 p90=15.3177 p99=21.1776 p99.9=27.4416 max=32.1146 mean=10.8247
T_addon_first_sse: n=31273 p50=10.6501 p90=15.601 p99=29.6727 p99.9=37.2972 max=65.7658 mean=10.9517
T_addon_total_sse: n=31273 p50=10.7171 p90=15.2943 p99=21.4027 p99.9=27.6492 max=37.8881 mean=10.7333
T_addon_total_json: n=13388 p50=10.8112 p90=15.3177 p99=21.1776 p99.9=27.4416 max=32.1146 mean=10.8247
T_release_lag_max: n=3196 p50=50.6158 p90=58.2027 p99=74.1378 p99.9=79.8224 max=92.6822 mean=50.9005
client_ttft_sse: n=31273 p50=160.6939 p90=165.6506 p99=179.7126 p99.9=187.3391 max=215.773 mean=160.9956
lateness: n=45542 p50=0.0832 p90=0.0935 p99=0.1055 p99.9=0.1254 max=0.269 mean=0.0826
loadgen: [{'vm': 'rv-pbu-lg-1', 'busy_max': 10.1, 'busy_mean': 6.9, 'late_max_us': 237, 'conn_opens': 433, 'max_inflight': 410}, {'vm': 'rv-pbu-lg-2', 'busy_max': 7.2, 'busy_mean': 6.6, 'late_max_us': 277, 'conn_opens': 434, 'max_inflight': 404}]
wire per client request: {'client_to_gw': 8238.8, 'gw_to_client': 56247.6, 'gw_to_provider': 8991.4, 'provider_to_gw': 56924.2}
gateway cores 5.326 cpu-ms/req 35.203 (workers 31.17, owners 4.03, redis 0.17)
worker util {'n': 18, 'min': 0.212, 'median': 0.263, 'max': 0.315}; per-core schedstat max 0.247 mean 0.227; procstat max 0.37
gpu: {'0': {'samples': 298, 'sm_mean': 22.9, 'sm_max': 59.0, 'mem_mean': 4.1, 'fb_mb_max': 434.0}, '1': {'samples': 298, 'sm_mean': 22.6, 'sm_max': 45.0, 'mem_mean': 4.1, 'fb_mb_max': 434.0}}
rss max total by role (MB): {'redis': 551.9, 'launcher': 59.8, 'owner': 3064.3, 'worker': 3645.4}; fds {'redis': 0, 'launcher': 7, 'owner': 126, 'worker': 2037}
W t_input_ns: {'n': 45440, 'mean_ms': 9.0927, 'p50_ms': 9.2406, 'p90_ms': 13.4349, 'p99_ms': 18.4812, 'p99.9_ms': 23.1997, 'max_cum_ms': 29.3039}
W t_admit_ns: {'n': 45521, 'mean_ms': 0.0943, 'p50_ms': 0.0886, 'p90_ms': 0.1132, 'p99_ms': 0.1485, 'p99.9_ms': 0.7496, 'max_cum_ms': 7.8784}
W t_tokenize_ns: {'n': 45521, 'mean_ms': 3.2784, 'p50_ms': 3.2604, 'p90_ms': 5.1446, 'p99_ms': 6.2587, 'p99.9_ms': 7.1762, 'max_cum_ms': 8.0973}
W t_det_scan_ns: {'n': 45439, 'mean_ms': 0.1152, 'p50_ms': 0.1101, 'p90_ms': 0.1587, 'p99_ms': 0.2038, 'p99.9_ms': 0.2468, 'max_cum_ms': 2.3906}
W t_guard_wait_ns: {'n': 45440, 'mean_ms': 5.1871, 'p50_ms': 5.0135, 'p90_ms': 8.0282, 'p99_ms': 13.0417, 'p99.9_ms': 17.6947, 'max_cum_ms': 24.3173}
W guard_owner_rtt_ns: {'n': 45440, 'mean_ms': 5.3556, 'p50_ms': 5.2101, 'p90_ms': 8.1592, 'p99_ms': 12.9106, 'p99.9_ms': 17.4326, 'max_cum_ms': 24.4904}
W guard_queue_ns: {'n': 45440, 'mean_ms': 0.8647, 'p50_ms': 0.1203, 'p90_ms': 3.1949, 'p99_ms': 7.6349, 'p99.9_ms': 11.862, 'max_cum_ms': 19.0055}
W guard_exec_ns: {'n': 45440, 'mean_ms': 3.5941, 'p50_ms': 4.2926, 'p90_ms': 4.5548, 'p99_ms': 6.6519, 'p99.9_ms': 6.8485, 'max_cum_ms': 7.8359}
W dispatch_headers_ns: {'n': 44637, 'mean_ms': 1502.228, 'p50_ms': 149.9464, 'p90_ms': 5804.9167, 'p99_ms': 7952.4004, 'p99.9_ms': 8153.727, 'max_cum_ms': 8137.9973}
W release_lag_ns: {'n': 6947686, 'mean_ms': 19.8879, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.6323, 'max_cum_ms': 83.5376}
W holdback_wait_ns: {'n': 6770702, 'mean_ms': 20.3403, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.6323, 'max_cum_ms': 83.4736}
W release_processing_ns: {'n': 6947686, 'mean_ms': 0.0658, 'p50_ms': 0.0643, 'p90_ms': 0.0876, 'p99_ms': 0.1152, 'p99.9_ms': 0.1464, 'max_cum_ms': 2.8966}
W t_finalize_ns: {'n': 44599, 'mean_ms': 0.2692, 'p50_ms': 0.2642, 'p90_ms': 0.3379, 'p99_ms': 0.3953, 'p99.9_ms': 0.4403, 'max_cum_ms': 2.0704}
W loop_lag_ns: {'n': 53560, 'mean_ms': 0.8666, 'p50_ms': 0.0125, 'p90_ms': 3.5881, 'p99_ms': 9.2406, 'p99.9_ms': 12.6484, 'max_cum_ms': 19.1685}
W guard_windows_per_request: {'n': 45521, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_exec_ns: {'n': 45376, 'mean_ms': 3.5945, 'p50_ms': 4.2926, 'p90_ms': 4.5548, 'p99_ms': 6.6519, 'p99.9_ms': 6.8485, 'max_cum_ms': 7.8359}
O guard_batch_windows: {'n': 45376, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_queue_ns: {'n': 45376, 'mean_ms': 0.8649, 'p50_ms': 0.1203, 'p90_ms': 3.1949, 'p99_ms': 7.6349, 'p99.9_ms': 11.862, 'max_cum_ms': 19.0055}
worker counts: {'admitted': 45521, 'audit_enqueued': 90039, 'audit_written': 90039, 'background_round_trips': 10712, 'disposition_ALLOW': 44641, 'disposition_BLOCK': 799, 'guard_windows': 74568, 'lease_granted_tokens{org="org-a"}': 43347840, 'lease_refills': 211, 'provider_calls': 44641, 'provider_connections_opened': 7743, 'quota_admitted_tokens{org="org-a"}': 43027942, 'requests_by_round_trips{n="0"}': 45310, 'requests_by_round_trips{n="1"}': 211, 'shared_state_round_trips': 211, 'shed{reason="guard_queue"}': 82}
owner counts: {'guard_batches': 45376, 'guard_windows': 74471, 'owner_requests': 45376, 'owner_windows': 74471}
gauges: {'audit_queue_bound': [181], 'audit_completeness_ratio': [1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'guard_tokens_per_s': [26050.27758631763, 26605.448685725496], 'input_queue_cap': [7.0], 'guard_queue_cap_tokens': [2605.0, 2660.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 23445, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 234452.49827685865}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 23944, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 239449.03817152948}]
notes: []
