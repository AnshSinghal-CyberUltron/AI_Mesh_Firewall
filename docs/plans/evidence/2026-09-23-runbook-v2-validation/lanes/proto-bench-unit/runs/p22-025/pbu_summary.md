# p22-025: strict FAIL | load-knee PASS (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 7500 (25.0/s) qualified 7400 (24.67/s) FP-blocks 100 (0.01333) expected-blocks 0 infra 0 (0.0) drops 0 safety 0 detection-misses 0
by class: {'benign': {'qualified': 7400, 'policy_block_fp': 100}}
infra reasons: {}
infra detail: {}

T_fw_addon: n=7400 p50=9.7026 p90=13.9047 p99=51.8285 p99.9=70.3591 max=89.036 mean=11.8577
T_fw_addon_nohold: n=7400 p50=9.4635 p90=11.781 p99=14.435 p99.9=17.3709 max=22.362 mean=8.8255
T_fw_addon_sse: n=5174 p50=9.7713 p90=30.6148 p99=52.9001 p99.9=70.699 max=89.036 mean=13.1462
T_fw_addon_json: n=2226 p50=9.5786 p90=11.6705 p99=14.8937 p99.9=17.7063 max=19.1471 mean=8.8628
T_addon_first_sse: n=5174 p50=9.3502 p90=12.6389 p99=27.2516 p99.9=31.3763 max=36.3238 mean=9.0217
T_addon_total_sse: n=5174 p50=9.4168 p90=11.8121 p99=14.3529 p99.9=17.3623 max=22.362 mean=8.8095
T_addon_total_json: n=2226 p50=9.5786 p90=11.6705 p99=14.8937 p99.9=17.7063 max=19.1471 mean=8.8628
T_release_lag_max: n=523 p50=49.377 p90=52.8877 p99=70.699 p99.9=89.036 max=89.036 mean=48.5973
client_ttft_sse: n=5174 p50=159.4033 p90=162.6972 p99=177.3377 p99.9=181.4174 max=186.3382 mean=159.0706
lateness: n=7500 p50=0.0925 p90=0.1083 p99=0.1273 p99.9=0.1677 max=0.1994 mean=0.0933
loadgen: [{'vm': 'rv-pbu-lg-1', 'busy_max': 14.2, 'busy_mean': 4.2, 'late_max_us': 222, 'conn_opens': 74, 'max_inflight': 70}, {'vm': 'rv-pbu-lg-2', 'busy_max': 17.6, 'busy_mean': 4.2, 'late_max_us': 312, 'conn_opens': 74, 'max_inflight': 70}]
wire per client request: {'client_to_gw': 9180.7, 'gw_to_client': 56892.8, 'gw_to_provider': 9760.8, 'provider_to_gw': 57544.2}
gateway cores None cpu-ms/req None (workers None, owners None, redis None)
worker util None; per-core schedstat max None mean None; procstat max None
gpu: None
rss max total by role (MB): None; fds None
worker counts: None
owner counts: None
gauges: None owners: None
notes: None
