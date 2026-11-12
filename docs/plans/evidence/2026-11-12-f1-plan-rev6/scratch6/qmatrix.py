import regex, sys, time, resource
D = int(sys.argv[1])
for g in ("(?:", "(", "(?>"):
    row = []
    for q in ("+", "*", "?", "{1,3}", "{2}", "+?", "*?", "{1,3}?", "++", "{0,1000}"):
        p = g * D + "a" + (")" + q) * D
        regex.purge(); t0 = time.thread_time(); regex.compile(p); row.append(f"{q}:{(time.thread_time()-t0)*1e3:.1f}")
    print(f"D={D} {g:4}", " ".join(row), flush=True)
print("maxrss MiB", resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024)
