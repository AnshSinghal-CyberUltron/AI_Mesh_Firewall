# c10b-ovl overload: base 97/s, burst total 414/s for 120s
pre: offered 97.0/s qualified 95.2/s shed 0.0 other infra 0.0 admitted C4 p50/p99/p99.9 7.712/11.521/12.262 (SSE 11.521, JSON 11.499) shed rt p99 None reasons {} other {}
burst: offered 414.0/s qualified 262.7/s shed 0.3552 other infra 0.0 admitted C4 p50/p99/p99.9 12.697/16.702/20.423 (SSE 16.775, JSON 16.513) shed rt p99 3.936 reasons {'olg:http_503 type=server_overloaded code=overloaded': 17646} other {}
post: offered 97.0/s qualified 95.3/s shed 0.0 other infra 0.0 admitted C4 p50/p99/p99.9 7.607/11.075/11.742 (SSE 11.074, JSON 11.092) shed rt p99 None reasons {} other {}
recovery {'bin_s': 5.0, 'first_ok_s_after_burst_end': 0.0, 'sustained_s_after_burst_end': 0.0}  drops {}  FAIL_OPEN evidence none
probe {'n': 315, 'status': {'200': 241, '503': 74}, 'status_retry_after': {'200|ra=|has_ms=False': 241, '503|ra=1|has_ms=True': 74}, 'retry_after_ms': {'n': 74, 'min': 5, 'p50': 8, 'max': 11}}
sut {'rv-r2unit-guard-1': {'dmesg_new_lines': 0, 'oom_lines': [], 'rvproto_pids_before_after': [2, 2], 'pids_unchanged': True}, 'rv-r2unit-gw-1': {'dmesg_new_lines': 0, 'oom_lines': [], 'rvproto_pids_before_after': [13, 13], 'pids_unchanged': True}}
counters {'rv-r2unit-guard-1': {'owner:owner_shed': 17720, 'owner:owner_shed_reason{reason="queue_bound"}': 17720}, 'rv-r2unit-gw-1': {'admitted': 79088, 'guard_owner_sheds': 17720, 'quota_admitted_tokens{org="org-a"}': 74542443, 'shed{reason="guard_owner_queue"}': 17720}}
