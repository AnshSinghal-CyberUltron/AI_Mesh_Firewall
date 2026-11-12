import cProfile, pstats, regex, sys, time
D = int(sys.argv[1])
for name, p in [("raw (a)+", "(" * D + "a" + ")+" * D), ("raw (?:a)+", "(?:" * D + "a" + ")+" * D),
                ("raw (?:a)?", "(?:" * D + "a" + ")?" * D), ("raw (?:a)*?", "(?:" * D + "a" + ")*?" * D),
                ("raw (?=", "(?=" * D + "a" + ")" * D), ("raw (?:a|b)", "(?:a|" * D + "b" + ")" * D),
                ("raw (?:(?:a)+b)", "(?:" * D + "a" + ")+b" * D), ("raw (?>a)+", "(?>" * D + "a" + ")+" * D)]:
    regex.purge()
    t0 = time.thread_time(); regex.compile(p); print(f"{name:22} D={D} compile {(time.thread_time()-t0)*1e3:8.1f} ms")
regex.purge()
p = "(" * D + "a" + ")+" * D
pr = cProfile.Profile(); pr.enable(); regex.compile(p); pr.disable()
pstats.Stats(pr).sort_stats("ncalls").print_stats(8)
