# x8-60: strict FAIL | load-knee PASS (sut, units=1, rate=60)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 3600 (60.0/s) qualified 3549 (59.15/s) FP-blocks 50 (0.01389) infra 1 (0.0002777777777777778) drops 0 safety 0
infra reasons: {'http_503': 1, 'incomplete': 1, 'unjoined': 1, 'disposition_missing': 1, 'stage_canon_missing': 1, 'stage_det_missing': 1, 'stage_sem_missing': 1, 'stage_resolve_missing': 1, 'stage_dispatch_missing': 1, 'stage_out_missing': 1, 'stage_audit_missing': 1}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 1}

T_fw_addon: n=3549 p50=11.1046 p90=17.3087 p99=54.5839 p99.9=72.4311 max=75.1226 mean=13.4322
T_fw_addon_nohold: n=3549 p50=10.7779 p90=14.1924 p99=18.5162 p99.9=22.7325 max=28.4386 mean=10.385
T_fw_addon_sse: n=2482 p50=11.2197 p90=32.7508 p99=56.1791 p99.9=72.5988 max=75.1226 mean=14.7294
T_fw_addon_json: n=1067 p50=10.8628 p90=14.0844 p99=19.334 p99.9=22.7112 max=23.5447 mean=10.4148
T_addon_first_sse: n=2482 p50=10.6661 p90=14.3758 p99=28.2181 p99.9=36.4013 max=46.627 mean=10.5279
T_addon_total_sse: n=2482 p50=10.7409 p90=14.2951 p99=17.9723 p99.9=22.7325 max=28.4386 mean=10.3722
T_addon_total_json: n=1067 p50=10.8628 p90=14.0844 p99=19.334 p99.9=22.7112 max=23.5447 mean=10.4148
T_release_lag_max: n=246 p50=50.5458 p90=56.1791 p99=72.5988 p99.9=75.1226 max=75.1226 mean=50.1279
lateness: n=3600 p50=0.0862 p90=0.0964 p99=0.1061 p99.9=0.12 max=0.1289 mean=0.0861
loadgen: [{'vm': 'rv-pbf-lg-4', 'busy_max': 14.3, 'busy_mean': 5.8, 'late_max_us': 236, 'conn_opens': 305, 'max_inflight': 305}]  provider_cpu_busy_max: 17.669228658707624
gateway cores total 2.04 cpu-ms/req {'gateway': 34.546, 'workers': 30.473, 'owners': 4.068}
  rv-pbf-unit-6: cores {'launcher': 0.0, 'owner': 0.24, 'owner0': 0.24, 'redis': 0.001, 'worker': 1.798} worker util max 0.339 per-core max 0.273 mean 0.259 gpu {'0': {'n': 60, 'sm_mean': 18.4, 'sm_p95': 22.0, 'sm_max': 26.0}} t_input_p99 14.0902 per-worker admitted {'n': 6, 'min': 480, 'max': 680, 'mean': 595.2, 'max_over_mean': 1.143, 'sheds_per_worker': [0, 0, 0, 0, 0, 1]}
W t_input_ns: {'n': 3571, 'mean_ms': 8.039, 'p50_ms': 8.7163, 'p90_ms': 11.2067, 'p99_ms': 14.0902, 'p99.9_ms': 17.4326, 'max_cum_ms': 30.0859}
W t_tokenize_ns: {'n': 3571, 'mean_ms': 3.1342, 'p50_ms': 3.1293, 'p90_ms': 5.0135, 'p99_ms': 5.8655, 'p99.9_ms': 6.5208, 'max_cum_ms': 8.4984}
W t_guard_wait_ns: {'n': 3571, 'mean_ms': 4.288, 'p50_ms': 4.8824, 'p90_ms': 5.3412, 'p99_ms': 7.5694, 'p99.9_ms': 12.5174, 'max_cum_ms': 17.2746}
W guard_owner_rtt_ns: {'n': 3571, 'mean_ms': 4.4711, 'p50_ms': 5.079, 'p90_ms': 5.5378, 'p99_ms': 7.8316, 'p99.9_ms': 11.7309, 'max_cum_ms': 16.3363}
W guard_queue_ns: {'n': 3571, 'mean_ms': 0.1091, 'p50_ms': 0.105, 'p90_ms': 0.1341, 'p99_ms': 0.1669, 'p99.9_ms': 0.3707, 'max_cum_ms': 2.5831}
W guard_exec_ns: {'n': 3571, 'mean_ms': 3.5896, 'p50_ms': 4.2926, 'p90_ms': 4.4892, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 9.3512}
W t_admit_ns: {'n': 3571, 'mean_ms': 0.1003, 'p50_ms': 0.0865, 'p90_ms': 0.1111, 'p99_ms': 0.8397, 'p99.9_ms': 1.1715, 'max_cum_ms': 7.9768}
W release_processing_ns: {'n': 547243, 'mean_ms': 0.0646, 'p50_ms': 0.0637, 'p90_ms': 0.0855, 'p99_ms': 0.1142, 'p99.9_ms': 0.1464, 'max_cum_ms': 2.5102}
W loop_lag_ns: {'n': 3550, 'mean_ms': 0.8667, 'p50_ms': 0.0065, 'p90_ms': 3.457, 'p99_ms': 9.7649, 'p99.9_ms': 11.2067, 'max_cum_ms': 14.3911}
W audit_batch_write_ns: {'n': 7089, 'mean_ms': 1.126, 'p50_ms': 0.9871, 'p90_ms': 1.3517, 'p99_ms': 5.6689, 'p99.9_ms': 10.9445, 'max_cum_ms': 17.5858}
W counts: {"admitted": 3571, "audit_enqueued": 7112, "audit_written": 7112, "background_round_trips": 710, "disposition_ALLOW": 3522, "disposition_BLOCK": 49, "guard_windows": 5888, "lease_refills": 50, "provider_calls": 3522, "provider_connections_opened": 356, "requests_by_round_trips{n=\"0\"}": 3521, "requests_by_round_trips{n=\"1\"}": 50, "shared_state_round_trips": 50, "shed{reason=\"guard_queue\"}": 1}
edge: null nginx cpu-ms/req None
redis: ops/s 277.1 ops/req 4.617 cpu cores 0.004 clients 46 mem 83.8MB ping(us) {'n': 5620, 'p50_us': 573.9, 'p90_us': 600.1, 'p99_us': 684.0, 'p99.9_us': 1904.3, 'max_us': 2932.9, 'mean_us': 579.4}
redis cmdstats: {'get': {'calls_per_s': 0.8, 'usec_per_call': 0.86}, 'info': {'calls_per_s': 0.0, 'usec_per_call': 134.0}, 'xadd': {'calls_per_s': 118.9, 'usec_per_call': 2.81}, 'hgetall': {'calls_per_s': 44.8, 'usec_per_call': 0.26}, 'ping': {'calls_per_s': 92.9, 'usec_per_call': 0.11}, 'decrby': {'calls_per_s': 0.8, 'usec_per_call': 0.58}, 'mget': {'calls_per_s': 17.9, 'usec_per_call': 0.44}, 'evalsha': {'calls_per_s': 0.8, 'usec_per_call': 19.58}}
wire: {'requests_in_window': 3600, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56168.2, 'client_side_to_unit': 8040.2, 'unit_to_provider': 8558.6, 'provider_to_unit': 56857.7, 'unit_to_redis': 5508.0, 'redis_to_unit': 368.1}, 'olg_resp_body_bytes_mean': {'sse': 66872.7, 'json': 1431.0}}
