# x1u-200: strict FAIL | load-knee FAIL (sut, units=1, rate=200)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': False, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 12000 (200.0/s) qualified 11301 (188.35/s) FP-blocks 185 (0.01542) infra 514 (0.042833333333333334) drops 0 safety 0
infra reasons: {'http_503': 514, 'incomplete': 514, 'unjoined': 514, 'disposition_missing': 514, 'stage_canon_missing': 514, 'stage_det_missing': 514, 'stage_sem_missing': 514, 'stage_resolve_missing': 514, 'stage_dispatch_missing': 514, 'stage_out_missing': 514, 'stage_audit_missing': 514}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 514}

T_fw_addon: n=11301 p50=11.7288 p90=18.8177 p99=55.7048 p99.9=72.6404 max=91.2188 mean=14.2289
T_fw_addon_nohold: n=11301 p50=11.3805 p90=15.4671 p99=20.6013 p99.9=27.0155 max=30.0325 mean=11.2476
T_fw_addon_sse: n=7893 p50=11.819 p90=31.8581 p99=57.572 p99.9=74.3456 max=91.2188 mean=15.4497
T_fw_addon_json: n=3408 p50=11.5443 p90=15.5847 p99=21.2009 p99.9=28.0143 max=30.0325 mean=11.4014
T_addon_first_sse: n=7893 p50=11.2432 p90=15.854 p99=31.062 p99.9=42.7929 max=52.6567 mean=11.469
T_addon_total_sse: n=7893 p50=11.3033 p90=15.4207 p99=20.5601 p99.9=25.3826 max=27.9319 mean=11.1811
T_addon_total_json: n=3408 p50=11.5443 p90=15.5847 p99=21.2009 p99.9=28.0143 max=30.0325 mean=11.4014
T_release_lag_max: n=737 p50=51.321 p90=57.7923 p99=74.3456 p99.9=91.2188 max=91.2188 mean=51.2391
lateness: n=12000 p50=0.0865 p90=0.0956 p99=0.1056 p99.9=0.1171 max=0.1912 mean=0.0863
loadgen: [{'vm': 'rv-pbf-lg-1', 'busy_max': 6.1, 'busy_mean': 5.6, 'late_max_us': 388, 'conn_opens': 327, 'max_inflight': 327}, {'vm': 'rv-pbf-lg-2', 'busy_max': 5.9, 'busy_mean': 5.4, 'late_max_us': 232, 'conn_opens': 329, 'max_inflight': 329}, {'vm': 'rv-pbf-lg-3', 'busy_max': 5.9, 'busy_mean': 5.4, 'late_max_us': 191, 'conn_opens': 323, 'max_inflight': 323}]  provider_cpu_busy_max: 16.704615117545675
gateway cores total 6.6 cpu-ms/req {'gateway': 33.569, 'workers': 29.68, 'owners': 3.887}
  rv-pbf-unit-2: cores {'launcher': 0.0, 'owner': 0.765, 'owner0': 0.364, 'owner1': 0.4, 'redis': 0.001, 'worker': 5.839} worker util max 0.404 per-core max 0.308 mean 0.279 gpu {'0': {'n': 61, 'sm_mean': 27.4, 'sm_p95': 39.0, 'sm_max': 54.0}, '1': {'n': 61, 'sm_mean': 28.1, 'sm_p95': 39.0, 'sm_max': 43.0}} t_input_p99 16.5806
W t_input_ns: {'n': 11416, 'mean_ms': 8.6182, 'p50_ms': 9.1095, 'p90_ms': 12.3863, 'p99_ms': 16.5806, 'p99.9_ms': 21.1026, 'max_cum_ms': 26.5138}
W t_tokenize_ns: {'n': 11928, 'mean_ms': 3.3394, 'p50_ms': 3.3915, 'p90_ms': 5.2101, 'p99_ms': 6.2587, 'p99.9_ms': 6.9796, 'max_cum_ms': 8.9355}
W t_guard_wait_ns: {'n': 11416, 'mean_ms': 4.6307, 'p50_ms': 4.948, 'p90_ms': 7.1107, 'p99_ms': 10.8134, 'p99.9_ms': 15.2699, 'max_cum_ms': 20.0884}
W guard_owner_rtt_ns: {'n': 11416, 'mean_ms': 4.802, 'p50_ms': 5.1446, 'p90_ms': 7.3728, 'p99_ms': 10.6824, 'p99.9_ms': 14.7456, 'max_cum_ms': 17.9609}
W guard_queue_ns: {'n': 11416, 'mean_ms': 0.3048, 'p50_ms': 0.1101, 'p90_ms': 0.4813, 'p99_ms': 4.0141, 'p99.9_ms': 7.3728, 'max_cum_ms': 10.52}
W guard_exec_ns: {'n': 11416, 'mean_ms': 3.6016, 'p50_ms': 4.2926, 'p90_ms': 4.5548, 'p99_ms': 6.6519, 'p99.9_ms': 6.8485, 'max_cum_ms': 7.4903}
W t_admit_ns: {'n': 11928, 'mean_ms': 0.0964, 'p50_ms': 0.0896, 'p90_ms': 0.1132, 'p99_ms': 0.1444, 'p99.9_ms': 1.2534, 'max_cum_ms': 8.8468}
W release_processing_ns: {'n': 1742413, 'mean_ms': 0.0657, 'p50_ms': 0.0648, 'p90_ms': 0.0876, 'p99_ms': 0.1162, 'p99.9_ms': 0.1485, 'max_cum_ms': 3.3503}
W loop_lag_ns: {'n': 10690, 'mean_ms': 0.8647, 'p50_ms': 0.0138, 'p90_ms': 3.6209, 'p99_ms': 9.3716, 'p99.9_ms': 12.3863, 'max_cum_ms': 21.1209}
W audit_batch_write_ns: {'n': 22663, 'mean_ms': 1.2623, 'p50_ms': 1.1059, 'p90_ms': 1.4991, 'p99_ms': 5.9965, 'p99.9_ms': 13.0417, 'max_cum_ms': 18.7758}
W counts: {"admitted": 11928, "audit_enqueued": 22744, "audit_written": 22742, "background_round_trips": 2138, "disposition_ALLOW": 11231, "disposition_BLOCK": 185, "guard_windows": 18809, "lease_refills": 55, "provider_calls": 11231, "provider_connections_opened": 1919, "requests_by_round_trips{n=\"0\"}": 11873, "requests_by_round_trips{n=\"1\"}": 55, "shared_state_round_trips": 55, "shed{reason=\"guard_queue\"}": 511}
edge: {"window_s": 61.0, "cores_by_role": {"nginx": 0.268}, "per_proc_util": {"nginx": [0.0, 0.013, 0.013, 0.014, 0.014, 0.015, 0.016, 0.017, 0.017, 0.017, 0.018, 0.018, 0.018, 0.018, 0.019, 0.019, 0.021]}, "per_core_util": {"max": 0.019, "mean": 0.017, "sum": 0.27, "n": 16}, "nic": null, "restarted": [], "loadavg_max": 0.25} nginx cpu-ms/req 1.363
redis: ops/s 1040.2 ops/req 5.201 cpu cores 0.011 clients 280 mem 115.8MB ping(us) {'n': 5644, 'p50_us': 512.2, 'p90_us': 544.7, 'p99_us': 644.7, 'p99.9_us': 2744.6, 'max_us': 3448.5, 'mean_us': 522.3}
redis cmdstats: {'info': {'calls_per_s': 0.0, 'usec_per_call': 123.0}, 'ping': {'calls_per_s': 93.3, 'usec_per_call': 0.12}, 'evalsha': {'calls_per_s': 0.9, 'usec_per_call': 10.74}, 'xadd': {'calls_per_s': 378.7, 'usec_per_call': 3.87}, 'mget': {'calls_per_s': 161.6, 'usec_per_call': 0.39}, 'hgetall': {'calls_per_s': 403.9, 'usec_per_call': 0.2}, 'get': {'calls_per_s': 0.9, 'usec_per_call': 0.57}, 'decrby': {'calls_per_s': 0.9, 'usec_per_call': 0.37}}
wire: {'requests_in_window': 12000, 'unit_ip_bytes_per_req': {'unit_to_client_side': 53498.4, 'client_side_to_unit': 7830.3, 'unit_to_provider': 8275.3, 'provider_to_unit': 54145.9, 'unit_to_redis': 5195.5, 'redis_to_unit': 289.8}, 'edge_ip_bytes_per_req': {'edge_to_clients': 53641.9, 'clients_to_edge': 8006.6}, 'olg_resp_body_bytes_mean': {'sse': 67306.5, 'json': 1429.2}}
