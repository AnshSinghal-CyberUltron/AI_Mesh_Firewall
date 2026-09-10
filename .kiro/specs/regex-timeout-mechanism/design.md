# Task 2 — design

## Thread-local persistent worker

One long-lived worker thread **per calling thread**, held in `threading.local()`.

- satisfies R3 by construction: each caller owns its queues, so there is no shared lock
  and no cross-thread contention;
- with a scanner pool of 4 this is ~5 worker threads for the process lifetime, replacing
  ~45 thread creations *per request*.

```python
_worker_tls = threading.local()

class _RegexWorker:
    def __init__(self):
        self._jobs = queue.SimpleQueue()
        self._outs = queue.SimpleQueue()
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            fn = self._jobs.get()
            try:
                self._outs.put((True, fn()))
            except Exception:
                self._outs.put((False, None))     # R2

    def run(self, fn, timeout):
        self._jobs.put(fn)
        try:
            ok, val = self._outs.get(timeout=timeout)   # P1
        except queue.Empty:
            return _TIMEOUT
        return val if ok else None
```

## The P3 trap and how it is handled

A hung job blocks its worker forever. If the worker were reused, every later regex would
queue behind it and time out — turning one bad pattern into a total detection outage,
strictly worse than today.

So on timeout the worker is **retired**: dropped from thread-local storage and replaced
on the next call. The stuck thread is abandoned exactly as the per-regex version abandoned
its daemon thread, and its queues go with it, so a late result cannot be mistaken for the
next call's answer. The expensive path (create a thread) now runs only when a regex
actually hangs, which is the rare case; the common path is a queue round-trip.

Self-healing falls out: if a worker dies for any reason, the next call times out, retires
it, and gets a fresh one.

## Why not the obvious alternatives

| option | rejected because |
|---|---|
| one thread per *evaluation* instead of per regex | a single hang would skip every remaining rule in that request — fail-open on 44 of 45 rules. Detection loss, not just a perf trade. |
| drop the thread; run inline with a deadline between regexes | loses P1 outright: a backtracking pattern blocks the request thread with no escape. |
| one shared worker pool for all threads | violates R3 — trades thread-creation contention for queue-lock contention, the thing being removed. |

## Verification

1. `test_regex_timeout_mechanism.py` — P1/P2/P3 with a function that genuinely blocks,
   plus worker reuse (the same worker object serves consecutive calls) and self-healing.
2. Policy-engine verdict equivalence over the corpus.
3. Full gateway suite — zero new failures.
4. Re-run the capacity sweep; report whether p99 collapsed, **including if it did not**.
