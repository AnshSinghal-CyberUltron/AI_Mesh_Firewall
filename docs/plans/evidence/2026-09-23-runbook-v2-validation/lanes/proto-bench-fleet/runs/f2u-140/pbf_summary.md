# f2u-140: strict FAIL | load-knee FAIL (sut, units=2, rate=140)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 42000 (140.0/s) qualified 40656 (135.52/s) FP-blocks 771 (0.01836) infra 573 (0.013642857142857142) drops 0 safety 0
infra reasons: {'http_503': 572, 'incomplete': 572, 'unjoined': 572, 'disposition_missing': 572, 'stage_canon_missing': 572, 'stage_det_missing': 572, 'stage_sem_missing': 572, 'stage_resolve_missing': 572, 'stage_dispatch_missing': 572, 'stage_out_missing': 572, 'stage_audit_missing': 572, 'block_on_unavailable_sem': 1}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 572, '403 http_403 type=policy_violation code=blocked_by_policy': 1}

T_fw_addon: n=40656 p50=10.6968 p90=15.6271 p99=53.9884 p99.9=71.7773 max=90.7419 mean=12.9969
T_fw_addon_nohold: n=40656 p50=10.4302 p90=13.5437 p99=16.5164 p99.9=21.107 max=35.3735 mean=9.9379
T_fw_addon_sse: n=28468 p50=10.7993 p90=32.2427 p99=55.0532 p99.9=72.1293 max=90.7419 mean=14.2819
T_fw_addon_json: n=12188 p50=10.4696 p90=13.7159 p99=16.6693 p99.9=20.9369 max=24.3418 mean=9.9952
T_addon_first_sse: n=28468 p50=10.3536 p90=13.7439 p99=29.8785 p99.9=34.5546 max=58.1182 mean=10.1445
T_addon_total_sse: n=28468 p50=10.4187 p90=13.4474 p99=16.4313 p99.9=21.1152 max=35.3735 mean=9.9134
T_addon_total_json: n=12188 p50=10.4696 p90=13.7159 p99=16.6693 p99.9=20.9369 max=24.3418 mean=9.9952
T_release_lag_max: n=2856 p50=50.2271 p90=55.015 p99=72.1293 p99.9=76.5394 max=90.7419 mean=49.9538
lateness: n=42000 p50=0.0866 p90=0.0962 p99=0.1071 p99.9=0.1218 max=0.3385 mean=0.0863
loadgen: [{'vm': 'rv-pbf-lg-1', 'busy_max': 5.3, 'busy_mean': 4.9, 'late_max_us': 710, 'conn_opens': 252, 'max_inflight': 239}, {'vm': 'rv-pbf-lg-2', 'busy_max': 5.3, 'busy_mean': 4.9, 'late_max_us': 240, 'conn_opens': 254, 'max_inflight': 241}, {'vm': 'rv-pbf-lg-3', 'busy_max': 5.3, 'busy_mean': 4.9, 'late_max_us': 179, 'conn_opens': 252, 'max_inflight': 240}]  provider_cpu_busy_max: 19.228771959602465
gateway cores total 4.94 cpu-ms/req {'gateway': 35.4, 'workers': 31.351, 'owners': 4.042}
  rv-pbf-unit-2: cores {'launcher': 0.0, 'owner': 0.282, 'owner0': 0.14, 'owner1': 0.142, 'redis': 0.001, 'worker': 2.186} worker util max 0.153 per-core max 0.122 mean 0.106 gpu {'0': {'n': 299, 'sm_mean': 9.8, 'sm_p95': 20.0, 'sm_max': 26.0}, '1': {'n': 299, 'sm_mean': 10.3, 'sm_p95': 19.0, 'sm_max': 26.0}} t_input_p99 13.1727 per-worker admitted {'n': 18, 'min': 694, 'max': 1581, 'mean': 1166.1, 'max_over_mean': 1.356, 'sheds_per_worker': [5, 9, 10, 10, 10, 11, 11, 12, 12, 14, 15, 19, 19, 22, 24, 27, 29, 30]}
  rv-pbf-unit-3: cores {'launcher': 0.0, 'owner': 0.282, 'owner0': 0.148, 'owner1': 0.134, 'redis': 0.001, 'worker': 2.189} worker util max 0.157 per-core max 0.119 mean 0.105 gpu {'0': {'n': 299, 'sm_mean': 11.0, 'sm_p95': 20.0, 'sm_max': 27.0}, '1': {'n': 299, 'sm_mean': 9.6, 'sm_p95': 17.0, 'sm_max': 24.0}} t_input_p99 13.1727 per-worker admitted {'n': 18, 'min': 796, 'max': 1549, 'mean': 1165.2, 'max_over_mean': 1.329, 'sheds_per_worker': [8, 9, 9, 12, 13, 14, 14, 14, 15, 15, 15, 16, 17, 20, 21, 21, 24, 24]}
W t_input_ns: {'n': 41392, 'mean_ms': 7.7019, 'p50_ms': 8.3558, 'p90_ms': 10.6824, 'p99_ms': 13.1727, 'p99.9_ms': 17.4326, 'max_cum_ms': 36.429}
W t_tokenize_ns: {'n': 41962, 'mean_ms': 2.8614, 'p50_ms': 2.8672, 'p90_ms': 4.4237, 'p99_ms': 5.1446, 'p99.9_ms': 6.2587, 'max_cum_ms': 8.9355}
W t_guard_wait_ns: {'n': 41392, 'mean_ms': 4.2832, 'p50_ms': 4.8169, 'p90_ms': 5.4067, 'p99_ms': 7.6349, 'p99.9_ms': 12.2552, 'max_cum_ms': 29.7032}
W guard_owner_rtt_ns: {'n': 41392, 'mean_ms': 4.4596, 'p50_ms': 5.0135, 'p90_ms': 5.6689, 'p99_ms': 7.8316, 'p99.9_ms': 11.5999, 'max_cum_ms': 28.7454}
W guard_queue_ns: {'n': 41391, 'mean_ms': 0.1257, 'p50_ms': 0.1101, 'p90_ms': 0.1362, 'p99_ms': 0.3461, 'p99.9_ms': 3.326, 'max_cum_ms': 16.1001}
W guard_exec_ns: {'n': 41391, 'mean_ms': 3.6044, 'p50_ms': 4.2271, 'p90_ms': 4.5548, 'p99_ms': 6.6519, 'p99.9_ms': 6.8485, 'max_cum_ms': 8.1548}
W t_admit_ns: {'n': 41962, 'mean_ms': 0.0941, 'p50_ms': 0.0886, 'p90_ms': 0.108, 'p99_ms': 0.1362, 'p99.9_ms': 1.0117, 'max_cum_ms': 21.7487}
W release_processing_ns: {'n': 6340010, 'mean_ms': 0.0643, 'p50_ms': 0.0637, 'p90_ms': 0.0824, 'p99_ms': 0.106, 'p99.9_ms': 0.1341, 'max_cum_ms': 26.4251}
W loop_lag_ns: {'n': 107230, 'mean_ms': 0.7554, 'p50_ms': 0.0076, 'p90_ms': 2.5068, 'p99_ms': 7.8316, 'p99.9_ms': 9.8959, 'max_cum_ms': 27.5291}
W audit_batch_write_ns: {'n': 81967, 'mean_ms': 1.01, 'p50_ms': 0.9462, 'p90_ms': 1.1878, 'p99_ms': 2.0726, 'p99.9_ms': 8.0937, 'max_cum_ms': 40.9418}
W counts: {"admitted": 41962, "audit_enqueued": 82006, "audit_written": 82008, "background_round_trips": 21446, "disposition_ALLOW": 40620, "disposition_BLOCK": 772, "guard_deadline_expired": 1, "guard_unavailable_findings": 1, "guard_windows": 68037, "lease_refills": 189, "provider_calls": 40620, "provider_connections_opened": 5735, "requests_by_round_trips{n=\"0\"}": 41773, "requests_by_round_trips{n=\"1\"}": 189, "shared_state_round_trips": 189, "shed{reason=\"guard_queue\"}": 570}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 0.171}, "per_proc_util": {"nginx": [0.0, 0.006, 0.009, 0.01, 0.01, 0.01, 0.01, 0.011, 0.011, 0.011, 0.011, 0.011, 0.012, 0.012, 0.012, 0.013, 0.014]}, "per_core_util": {"max": 0.012, "mean": 0.011, "sum": 0.17, "n": 16}, "nic": null, "restarted": [], "loadavg_max": 0.25} nginx cpu-ms/req 1.224
redis: ops/s 871.7 ops/req 6.226 cpu cores 0.008 clients 291 mem 287.3MB ping(us) {'n': 28468, 'p50_us': 427.1, 'p90_us': 456.9, 'p99_us': 563.0, 'p99.9_us': 2264.0, 'max_us': 4819.0, 'mean_us': 439.3}
redis cmdstats: {'info': {'calls_per_s': 0.0, 'usec_per_call': 127.0}, 'ping': {'calls_per_s': 94.7, 'usec_per_call': 0.1}, 'evalsha': {'calls_per_s': 0.6, 'usec_per_call': 11.01}, 'xadd': {'calls_per_s': 273.3, 'usec_per_call': 3.65}, 'mget': {'calls_per_s': 143.3, 'usec_per_call': 0.38}, 'hgetall': {'calls_per_s': 358.4, 'usec_per_call': 0.21}, 'get': {'calls_per_s': 0.6, 'usec_per_call': 0.58}, 'decrby': {'calls_per_s': 0.6, 'usec_per_call': 0.33}, 'hello': {'calls_per_s': 0.0, 'usec_per_call': 3.0}}
wire: {'requests_in_window': 42000, 'unit_ip_bytes_per_req': {'unit_to_client_side': 55508.6, 'client_side_to_unit': 8772.8, 'unit_to_provider': 9467.1, 'provider_to_unit': 56183.0, 'unit_to_redis': 5507.5, 'redis_to_unit': 356.5}, 'edge_ip_bytes_per_req': {'edge_to_clients': 55623.7, 'clients_to_edge': 8104.8}, 'olg_resp_body_bytes_mean': {'sse': 67623.4, 'json': 1425.1}}
