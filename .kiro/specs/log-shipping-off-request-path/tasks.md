# Task 5 — tasks

- [ ] 5.0 Measure first: count log records actually emitted per request at DEBUG, so the
      size of the problem is known before it is fixed
- [ ] 5.1 Test: logging from the request path performs no socket call on that thread
- [ ] 5.2 Test: a full queue drops and COUNTS; never blocks, never silently loses
- [ ] 5.3 Test: shutdown flushes
- [ ] 5.4 `QueueHandler` + `QueueListener` with a bounded queue and a drop counter
- [ ] 5.5 Make the DEBUG level env-configurable; remove the unconditional setLevel
- [ ] 5.6 Full suite — zero new failures
- [ ] 5.7 Sweep, attributing the level change and the queue SEPARATELY
