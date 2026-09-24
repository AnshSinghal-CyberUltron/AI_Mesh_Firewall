# direct-160-lg4: strict PASS | load-knee PASS (direct, units=0, rate=160)

checks: {'zero_schedule_drops': True, 'zero_errors': True, 'run_valid': True, 'loadgen_cpu_lt_70': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 48000 (160.0/s) qualified 48000 (160.0/s) FP-blocks 0 (0.0) infra 0 (0.0) drops 0 safety 0
infra reasons: {}
infra detail: {}

T_fw_addon: n=48000 p50=0.1561 p90=0.1893 p99=0.2201 p99.9=0.3067 max=3.0048 mean=0.1588
T_fw_addon_nohold: n=48000 p50=0.1445 p90=0.1764 p99=0.2052 p99.9=0.2474 max=0.6013 mean=0.146
T_fw_addon_sse: n=33600 p50=0.1547 p90=0.1907 p99=0.222 p99.9=0.322 max=3.0048 mean=0.1581
T_fw_addon_json: n=14400 p50=0.1588 p90=0.1864 p99=0.2131 p99.9=0.2652 max=0.6013 mean=0.1604
T_addon_first_sse: n=33600 p50=0.1439 p90=0.174 p99=0.2015 p99.9=0.2433 max=0.5061 mean=0.1458
T_addon_total_sse: n=33600 p50=0.1378 p90=0.1686 p99=0.1969 p99.9=0.239 max=0.4547 mean=0.1398
T_addon_total_json: n=14400 p50=0.1588 p90=0.1864 p99=0.2131 p99.9=0.2652 max=0.6013 mean=0.1604
T_release_lag_max: n=3368 p50=0.1951 p90=0.2198 p99=0.2567 p99.9=1.6998 max=3.0048 mean=0.1996
lateness: n=48000 p50=0.0832 p90=0.0926 p99=0.1026 p99.9=0.1157 max=0.3147 mean=0.0774
loadgen: [{'vm': 'rv-pbf-lg-4', 'busy_max': 10.6, 'busy_mean': 7.7, 'late_max_us': 1163, 'conn_opens': 913, 'max_inflight': 786}]  provider_cpu_busy_max: 10.540802823848239
