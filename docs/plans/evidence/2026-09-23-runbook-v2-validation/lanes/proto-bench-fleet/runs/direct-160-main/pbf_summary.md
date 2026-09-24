# direct-160-main: strict PASS | load-knee PASS (direct, units=0, rate=160)

checks: {'zero_schedule_drops': True, 'zero_errors': True, 'run_valid': True, 'loadgen_cpu_lt_70': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 48000 (160.0/s) qualified 48000 (160.0/s) FP-blocks 0 (0.0) infra 0 (0.0) drops 0 safety 0
infra reasons: {}
infra detail: {}

T_fw_addon: n=48000 p50=0.1395 p90=0.1664 p99=0.1933 p99.9=0.3469 max=6.5208 mean=0.1423
T_fw_addon_nohold: n=48000 p50=0.1298 p90=0.1606 p99=0.1893 p99.9=0.3112 max=6.5208 mean=0.1327
T_fw_addon_sse: n=33600 p50=0.1356 p90=0.1607 p99=0.1878 p99.9=0.3498 max=6.5208 mean=0.1388
T_fw_addon_json: n=14400 p50=0.1497 p90=0.1742 p99=0.1994 p99.9=0.3391 max=0.4023 mean=0.1506
T_addon_first_sse: n=33600 p50=0.1294 p90=0.1519 p99=0.1744 p99.9=0.3209 max=0.3957 mean=0.1307
T_addon_total_sse: n=33600 p50=0.1234 p90=0.1457 p99=0.1698 p99.9=0.3025 max=6.5208 mean=0.1251
T_addon_total_json: n=14400 p50=0.1497 p90=0.1742 p99=0.1994 p99.9=0.3391 max=0.4023 mean=0.1506
T_release_lag_max: n=3340 p50=0.1607 p90=0.1826 p99=0.2623 p99.9=3.3607 max=5.2494 mean=0.1715
lateness: n=48000 p50=0.0859 p90=0.0944 p99=0.1049 p99.9=0.1173 max=0.1941 mean=0.0839
loadgen: [{'vm': 'rv-pbf-lg-1', 'busy_max': 8.2, 'busy_mean': 5.1, 'late_max_us': 593, 'conn_opens': 401, 'max_inflight': 280}, {'vm': 'rv-pbf-lg-2', 'busy_max': 5.4, 'busy_mean': 4.9, 'late_max_us': 208, 'conn_opens': 390, 'max_inflight': 280}, {'vm': 'rv-pbf-lg-3', 'busy_max': 5.5, 'busy_mean': 5.1, 'late_max_us': 228, 'conn_opens': 384, 'max_inflight': 280}]  provider_cpu_busy_max: 19.637649128707046
