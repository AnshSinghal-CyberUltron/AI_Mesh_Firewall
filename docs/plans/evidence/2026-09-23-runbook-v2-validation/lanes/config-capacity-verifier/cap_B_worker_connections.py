"""Parse the gateway's EXACT gunicorn argv (entrypoint.sh at baseline) with gunicorn's own
parser, let gunicorn resolve the worker class, instantiate the real UvicornWorker and read
back the uvicorn Config it builds. Shows whether --worker-connections reaches uvicorn."""
import os
import warnings

warnings.simplefilter("always")
from gunicorn.config import Config
from gunicorn.glogging import Logger

ARGV = ["ai_mesh_gateway.main:app", "-k", "uvicorn.workers.UvicornWorker",
        "--bind", "0.0.0.0:8300", "--workers", "16", "--worker-connections", "20000",
        "--timeout", "120", "--graceful-timeout", "60", "--keep-alive", "65"]

cfg = Config()
args = cfg.parser().parse_args(ARGV)
for k, v in vars(args).items():
    if v is None or k == "args":
        continue
    cfg.set(k.lower(), v)
print("gunicorn cfg.worker_connections =", cfg.worker_connections)

with warnings.catch_warnings(record=True) as w:
    warnings.simplefilter("always")
    worker_cls = cfg.worker_class          # gunicorn imports uvicorn.workers here
    for x in w:
        if issubclass(x.category, DeprecationWarning):
            print("DeprecationWarning on import:", str(x.message).replace("\n", " "))
print("resolved worker class:", f"{worker_cls.__module__}.{worker_cls.__name__}")

worker = worker_cls(age=1, ppid=os.getpid(), sockets=[], app=None, timeout=120, cfg=cfg, log=Logger(cfg))
uc = worker.config
print("uvicorn Config.limit_concurrency =", uc.limit_concurrency)
print("uvicorn Config.timeout_keep_alive =", uc.timeout_keep_alive, "(mapped from --keep-alive)")
print("uvicorn Config.backlog =", uc.backlog)
print("worker has attribute worker_connections:", hasattr(worker, "worker_connections"))
