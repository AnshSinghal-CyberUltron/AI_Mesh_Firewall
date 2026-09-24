# f8-020-poisson: strict FAIL | load-knee FAIL (sut, units=1, rate=20)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 6034 (20.11/s) qualified 5810 (19.37/s) FP-blocks 87 (0.01442) infra 137 (0.022704673516738483) drops 0 safety 0
infra reasons: {'http_503': 137, 'incomplete': 137, 'unjoined': 137, 'disposition_missing': 137, 'stage_canon_missing': 137, 'stage_det_missing': 137, 'stage_sem_missing': 137, 'stage_resolve_missing': 137, 'stage_dispatch_missing': 137, 'stage_out_missing': 137, 'stage_audit_missing': 137}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 137}

T_fw_addon: n=5810 p50=10.6964 p90=16.5141 p99=53.6668 p99.9=72.0668 max=89.8044 mean=13.0907
T_fw_addon_nohold: n=5810 p50=10.4497 p90=13.8336 p99=17.2289 p99.9=22.0132 max=26.2925 mean=9.9985
T_fw_addon_sse: n=4065 p50=10.7901 p90=32.8496 p99=54.681 p99.9=72.2951 max=89.8044 mean=14.4113
T_fw_addon_json: n=1745 p50=10.4957 p90=13.916 p99=17.7455 p99.9=22.6333 max=26.2925 mean=10.0141
T_addon_first_sse: n=4065 p50=10.3519 p90=13.9348 p99=30.5741 p99.9=34.9076 max=52.0683 mean=10.2637
T_addon_total_sse: n=4065 p50=10.4329 p90=13.8267 p99=17.1894 p99.9=20.8811 max=23.6859 mean=9.9918
T_addon_total_json: n=1745 p50=10.4957 p90=13.916 p99=17.7455 p99.9=22.6333 max=26.2925 mean=10.0141
T_release_lag_max: n=407 p50=50.003 p90=54.681 p99=72.2951 p99.9=89.8044 max=89.8044 mean=49.65
lateness: n=6034 p50=0.0857 p90=0.0957 p99=0.1071 p99.9=0.1231 max=0.1517 mean=0.0855
loadgen: [{'vm': 'rv-pbf-lg-4', 'busy_max': 17.8, 'busy_mean': 4.0, 'late_max_us': 214, 'conn_opens': 127, 'max_inflight': 118}]  provider_cpu_busy_max: 21.588098260522315
gateway cores total 0.71 cpu-ms/req {'gateway': 35.662, 'workers': 31.563, 'owners': 4.084}
  rv-pbf-unit-6: cores {'launcher': 0.0, 'owner': 0.082, 'owner0': 0.082, 'redis': 0.001, 'worker': 0.633} worker util max 0.135 per-core max 0.101 mean 0.095 gpu {'0': {'n': 298, 'sm_mean': 5.9, 'sm_p95': 15.0, 'sm_max': 21.0}} t_input_p99 14.6145 per-worker admitted {'n': 6, 'min': 661, 'max': 1403, 'mean': 1003.7, 'max_over_mean': 1.398, 'sheds_per_worker': [7, 15, 16, 17, 38, 44]}
W t_input_ns: {'n': 5886, 'mean_ms': 7.9002, 'p50_ms': 8.4541, 'p90_ms': 11.5999, 'p99_ms': 14.6145, 'p99.9_ms': 18.7433, 'max_cum_ms': 44.8173}
W t_tokenize_ns: {'n': 6023, 'mean_ms': 2.8541, 'p50_ms': 2.8672, 'p90_ms': 4.4237, 'p99_ms': 5.2101, 'p99.9_ms': 6.5208, 'max_cum_ms': 8.9682}
W t_guard_wait_ns: {'n': 5886, 'mean_ms': 4.47, 'p50_ms': 4.8824, 'p90_ms': 6.914, 'p99_ms': 9.6338, 'p99.9_ms': 13.697, 'max_cum_ms': 38.8422}
W guard_owner_rtt_ns: {'n': 5886, 'mean_ms': 4.6481, 'p50_ms': 5.079, 'p90_ms': 7.1107, 'p99_ms': 9.6338, 'p99.9_ms': 13.697, 'max_cum_ms': 38.9155}
W guard_queue_ns: {'n': 5886, 'mean_ms': 0.2591, 'p50_ms': 0.1162, 'p90_ms': 0.1546, 'p99_ms': 4.0796, 'p99.9_ms': 7.3728, 'max_cum_ms': 10.8147}
W guard_exec_ns: {'n': 5886, 'mean_ms': 3.6414, 'p50_ms': 4.2271, 'p90_ms': 4.6203, 'p99_ms': 6.6519, 'p99.9_ms': 6.8485, 'max_cum_ms': 9.3512}
W t_admit_ns: {'n': 6022, 'mean_ms': 0.1029, 'p50_ms': 0.0886, 'p90_ms': 0.1101, 'p99_ms': 0.8806, 'p99.9_ms': 1.1878, 'max_cum_ms': 12.8768}
W release_processing_ns: {'n': 899129, 'mean_ms': 0.0644, 'p50_ms': 0.0637, 'p90_ms': 0.0824, 'p99_ms': 0.108, 'p99.9_ms': 0.1505, 'max_cum_ms': 30.7951}
W loop_lag_ns: {'n': 17860, 'mean_ms': 0.8006, 'p50_ms': 0.0055, 'p90_ms': 2.6706, 'p99_ms': 8.0937, 'p99.9_ms': 9.8959, 'max_cum_ms': 41.882}
W audit_batch_write_ns: {'n': 11689, 'mean_ms': 1.0232, 'p50_ms': 0.938, 'p90_ms': 1.2698, 'p99_ms': 2.474, 'p99.9_ms': 8.9784, 'max_cum_ms': 43.4425}
W counts: {"admitted": 6022, "audit_enqueued": 11697, "audit_written": 11697, "background_round_trips": 3572, "disposition_ALLOW": 5799, "disposition_BLOCK": 87, "guard_windows": 9700, "lease_refills": 83, "provider_calls": 5799, "provider_connections_opened": 628, "requests_by_round_trips{n=\"0\"}": 5939, "requests_by_round_trips{n=\"1\"}": 83, "shared_state_round_trips": 83, "shed{reason=\"guard_queue\"}": 137}
edge: null nginx cpu-ms/req None
redis: ops/s 197.1 ops/req 9.8 cpu cores 0.002 clients 47 mem 1186.7MB ping(us) {'n': 28466, 'p50_us': 434.0, 'p90_us': 471.7, 'p99_us': 589.1, 'p99.9_us': 1730.4, 'max_us': 5054.8, 'mean_us': 445.5}
redis cmdstats: {'get': {'calls_per_s': 0.3, 'usec_per_call': 0.75}, 'info': {'calls_per_s': 0.0, 'usec_per_call': 137.0}, 'xadd': {'calls_per_s': 39.0, 'usec_per_call': 3.45}, 'hgetall': {'calls_per_s': 44.7, 'usec_per_call': 0.25}, 'ping': {'calls_per_s': 94.7, 'usec_per_call': 0.11}, 'decrby': {'calls_per_s': 0.3, 'usec_per_call': 0.39}, 'mget': {'calls_per_s': 17.9, 'usec_per_call': 0.45}, 'evalsha': {'calls_per_s': 0.3, 'usec_per_call': 14.99}}
wire: {'requests_in_window': 6034, 'unit_ip_bytes_per_req': {'unit_to_client_side': 54796.9, 'client_side_to_unit': 8672.4, 'unit_to_provider': 9429.1, 'provider_to_unit': 55467.4, 'unit_to_redis': 5732.5, 'redis_to_unit': 603.9}, 'olg_resp_body_bytes_mean': {'sse': 67144.4, 'json': 1430.5}}
