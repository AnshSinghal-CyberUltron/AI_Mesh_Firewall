"""Gunicorn hooks for the gateway.

child_exit must call prometheus_client.multiprocess.mark_process_dead so
livesum gauges drop a dead worker's mmap files. No-op when
PROMETHEUS_MULTIPROC_DIR is unset.
"""


def child_exit(server, worker):  # noqa: ARG001 — gunicorn hook signature
    try:
        from prometheus_client import multiprocess

        multiprocess.mark_process_dead(worker.pid)
    except Exception:
        pass
