# Task 5 — design

## `QueueHandler` + `QueueListener`

The standard-library shape for exactly this problem:

- request-path loggers get a `logging.handlers.QueueHandler` writing to a **bounded**
  `queue.Queue`. Enqueue is a lock + append: no socket, no event-loop block (R1).
- a single `QueueListener` thread owns the real `RedisLogPublisher` and drains the queue.
  All Redis I/O happens there, off every request path.

## The bounded-queue decision (R2)

Unbounded would trade a latency bug for a memory bug: if Redis stalls, the queue grows
without limit until the process dies. Bounded, with a `maxsize` sized for a burst, and on
`queue.Full` the record is dropped and a counter incremented — then surfaced as a metric
and a periodic warning emitted from the listener thread, never from the request path.

Silent loss is the failure mode to avoid: it looks identical to "nothing happened",
which is the worst property a security audit log can have.

## The DEBUG level (R3)

`gateway_logger.setLevel(logging.DEBUG)` becomes
`gateway_logger.setLevel(_level_from_env(default="INFO"))`, honouring `GATEWAY_LOG_LEVEL`
so the overlay's setting finally means something. DEBUG stays reachable by setting it.

This is not merely cosmetic: at DEBUG the volume is what makes the synchronous publish
hurt. Both halves matter, and they are separable — so measure them **separately**, since
attributing the whole win to one when the other did the work is the class of error this
work keeps catching.

## Verification

1. A test asserting no socket call happens on the calling thread when a record is logged
   (patch the publisher; assert it is invoked from a different thread).
2. A test that a full queue drops and **counts**, rather than blocking or silently losing.
3. A shutdown test: records logged immediately before shutdown still reach the publisher.
4. Re-run the capacity sweep — separately for (a) level change alone and (b) queue alone,
   so each is attributed.
