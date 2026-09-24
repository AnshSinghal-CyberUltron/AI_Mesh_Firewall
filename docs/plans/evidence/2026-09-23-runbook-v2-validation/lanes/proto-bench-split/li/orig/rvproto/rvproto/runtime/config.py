"""Deployment-declared settings (env). Not capacity: capacity comes from contract.py.

Every value that is not set in the environment is reported in `defaulted` so the
startup log shows exactly which declarations were taken as defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields

_DEFAULTS: tuple[tuple[str, str], ...] = (
    ("RV_HOST", "0.0.0.0"),
    ("RV_PORT", "8400"),
    ("RV_REDIS_URL", "redis://127.0.0.1:16379/0"),
    ("RV_PROVIDER_URL", "http://127.0.0.1:18080"),
    ("RV_PROVIDER_KEY", ""),
    ("RV_PASSTHROUGH_PREFIX", "x-synth-"),
    ("RV_GUARD_BACKEND", "local_cpu"),
    ("RV_GUARD_MODEL", ""),
    ("RV_GUARD_TOKENIZER", ""),
    ("RV_GUARD_BATCH", "8"),
    ("RV_GUARD_WAIT_US", "1000"),
    ("RV_GUARD_DEADLINE_MS", ""),  # empty => the contract's target_p99_ms
    ("RV_GUARD_CPU_THREADS", "1"),
    ("RV_GUARD_SEQ_BUCKETS", "64,128,256,512"),
    ("RV_GUARD_ALLOW_CUDA_FALLBACK", "0"),
    ("RV_GUARD_N_GPUS", ""),  # empty => detected from the backend
    ("RV_TRT_CACHE_DIR", "/var/tmp/rv-trt-cache"),
    ("RV_TRITON_URL", "127.0.0.1:8001"),
    ("RV_TRITON_MODEL", "pg2"),
    ("RV_GUARD_WARMUP_DELAY_S", "0"),
    ("RV_WINDOW_TOKENS", "512"),
    ("RV_WINDOW_OVERLAP", "64"),
    ("RV_MAX_WINDOWS", "16"),
    ("RV_MAX_BODY_BYTES", "1048576"),
    ("RV_DECODE_BUDGET", "4096"),
    ("RV_DEFAULT_OUTPUT_TOKENS", "400"),
    ("RV_KS_REFRESH_MS", "500"),
    ("RV_KS_STALE_MS", "5000"),
    ("RV_PLAN_RECONCILE_MS", "1000"),
    ("RV_AUDIT_STREAM_MAXLEN", "2000000"),
    ("RV_INJECT_PREDISPATCH_MS", "0"),
    ("RV_INJECT_HOLD", ""),  # "<chunk_index>:<ms>"
    ("RV_ADMIN_HOOKS", "0"),
    ("RV_METRICS_DIR", ""),
    ("RV_MODELS", "gpt-4o-mini"),
    ("RV_PROVIDER_TIMEOUT_S", "120"),
    ("RV_LEASE_CHUNK_TOKENS", ""),  # empty => contract-derived; set => logged deviation
    ("RV_GUARD_TOPOLOGY", "in_process"),  # in_process (PROTO_SPEC) | owner (per GPU) | remote (split)
    ("RV_GUARD_SOCKET_DIR", ""),  # empty => $XDG_RUNTIME_DIR/rv-guard-<port> (sun_path is 108 bytes)
    # EXPERIMENT (proto-bench-unit holdback attribution only): off = the output phase
    # scans and holds nothing (stage reported out:S). Never a production posture.
    ("RV_EXP_OUTPUT_SCAN", "on"),
    ("RV_GUARD_OWNER_LISTEN", "unix"),  # owners: `unix` and/or `tcp://HOST:PORT` (owner i binds PORT+i)
    ("RV_GUARD_OWNER_ADDRS", ""),  # remote: host:port of EVERY owner the workers may use
    ("RV_GUARD_FLEET_WORKERS", ""),  # remote: workers fleet-wide sharing those owners (empty => this node's)
)
_SUN_PATH_MAX = 107  # sockaddr_un.sun_path minus the NUL terminator (Linux)


@dataclass(frozen=True, slots=True)
class Settings:
    host: str
    port: int
    redis_url: str
    provider_url: str
    provider_key: str
    passthrough_prefix: str
    guard_backend: str
    guard_model: str
    guard_tokenizer: str
    guard_batch: int
    guard_wait_us: int
    guard_deadline_ms: float | None
    guard_cpu_threads: int
    guard_seq_buckets: tuple[int, ...]
    guard_allow_cuda_fallback: bool
    guard_n_gpus: int | None
    trt_cache_dir: str
    triton_url: str
    triton_model: str
    guard_warmup_delay_s: float
    window_tokens: int
    window_overlap: int
    max_windows: int
    max_body_bytes: int
    decode_budget: int
    default_output_tokens: int
    ks_refresh_ms: float
    ks_stale_ms: float
    plan_reconcile_ms: float
    audit_stream_maxlen: int
    inject_predispatch_ms: float
    inject_hold: tuple[int, float] | None
    admin_hooks: bool
    metrics_dir: str
    models: tuple[str, ...]
    provider_timeout_s: float
    lease_chunk_tokens: int | None
    guard_topology: str
    guard_socket_dir: str
    exp_output_scan: bool
    guard_owner_listen_unix: bool
    guard_owner_listen_tcp: tuple[str, int] | None
    guard_owner_addrs: tuple[tuple[str, int], ...]
    guard_fleet_workers: int | None
    worker_index: int
    defaulted: tuple[str, ...]


def _hold(raw: str) -> tuple[int, float] | None:
    if not raw.strip():
        return None
    idx, ms = raw.split(":")
    return int(idx), float(ms)


def _hostport(raw: str) -> tuple[str, int]:
    host, sep, port = raw.strip().rpartition(":")
    if not sep or not host or not port.isdigit() or not 0 < int(port) < 65536:
        raise ValueError(f"expected host:port, got {raw!r}")
    return host.strip("[]"), int(port)


def _listen(spec: str) -> tuple[bool, tuple[str, int] | None]:
    unix, tcp = False, None
    for item in (x.strip() for x in spec.split(",") if x.strip()):
        if item == "unix":
            unix = True
        elif item.startswith("tcp://"):
            tcp = _hostport(item[len("tcp://") :])
        else:
            raise ValueError(f"RV_GUARD_OWNER_LISTEN item {item!r} (unix | tcp://HOST:PORT)")
    if not unix and tcp is None:
        raise ValueError("RV_GUARD_OWNER_LISTEN names no endpoint")
    return unix, tcp


def _socket_dir(src: dict[str, str], port: str) -> str:
    base = src.get("XDG_RUNTIME_DIR") or os.path.expanduser("~")
    return os.path.join(base, f"rv-guard-{port}")


def load_settings(env: dict[str, str] | None = None) -> Settings:
    src = dict(os.environ) if env is None else env
    get = {k: src.get(k, d) for k, d in _DEFAULTS}
    defaulted = tuple(k for k, _ in _DEFAULTS if k not in src)
    deadline = get["RV_GUARD_DEADLINE_MS"].strip()
    n_gpus = get["RV_GUARD_N_GPUS"].strip()
    listen_unix, listen_tcp = _listen(get["RV_GUARD_OWNER_LISTEN"])
    fleet = get["RV_GUARD_FLEET_WORKERS"].strip()
    s = Settings(
        host=get["RV_HOST"],
        port=int(get["RV_PORT"]),
        redis_url=get["RV_REDIS_URL"],
        provider_url=get["RV_PROVIDER_URL"].rstrip("/"),
        provider_key=get["RV_PROVIDER_KEY"],
        passthrough_prefix=get["RV_PASSTHROUGH_PREFIX"].lower(),
        guard_backend=get["RV_GUARD_BACKEND"],
        guard_model=get["RV_GUARD_MODEL"],
        guard_tokenizer=get["RV_GUARD_TOKENIZER"],
        guard_batch=int(get["RV_GUARD_BATCH"]),
        guard_wait_us=int(get["RV_GUARD_WAIT_US"]),
        guard_deadline_ms=float(deadline) if deadline else None,
        guard_cpu_threads=int(get["RV_GUARD_CPU_THREADS"]),
        guard_seq_buckets=tuple(int(x) for x in get["RV_GUARD_SEQ_BUCKETS"].split(",") if x),
        guard_allow_cuda_fallback=get["RV_GUARD_ALLOW_CUDA_FALLBACK"] == "1",
        guard_n_gpus=int(n_gpus) if n_gpus else None,
        trt_cache_dir=get["RV_TRT_CACHE_DIR"],
        triton_url=get["RV_TRITON_URL"],
        triton_model=get["RV_TRITON_MODEL"],
        guard_warmup_delay_s=float(get["RV_GUARD_WARMUP_DELAY_S"]),
        window_tokens=int(get["RV_WINDOW_TOKENS"]),
        window_overlap=int(get["RV_WINDOW_OVERLAP"]),
        max_windows=int(get["RV_MAX_WINDOWS"]),
        max_body_bytes=int(get["RV_MAX_BODY_BYTES"]),
        decode_budget=int(get["RV_DECODE_BUDGET"]),
        default_output_tokens=int(get["RV_DEFAULT_OUTPUT_TOKENS"]),
        ks_refresh_ms=float(get["RV_KS_REFRESH_MS"]),
        ks_stale_ms=float(get["RV_KS_STALE_MS"]),
        plan_reconcile_ms=float(get["RV_PLAN_RECONCILE_MS"]),
        audit_stream_maxlen=int(get["RV_AUDIT_STREAM_MAXLEN"]),
        inject_predispatch_ms=float(get["RV_INJECT_PREDISPATCH_MS"]),
        inject_hold=_hold(get["RV_INJECT_HOLD"]),
        admin_hooks=get["RV_ADMIN_HOOKS"] == "1",
        metrics_dir=get["RV_METRICS_DIR"],
        models=tuple(m for m in get["RV_MODELS"].split(",") if m),
        provider_timeout_s=float(get["RV_PROVIDER_TIMEOUT_S"]),
        lease_chunk_tokens=int(get["RV_LEASE_CHUNK_TOKENS"]) if get["RV_LEASE_CHUNK_TOKENS"].strip() else None,
        guard_topology=get["RV_GUARD_TOPOLOGY"],
        guard_socket_dir=get["RV_GUARD_SOCKET_DIR"] or _socket_dir(src, get["RV_PORT"]),
        exp_output_scan=get["RV_EXP_OUTPUT_SCAN"].strip().lower() != "off",
        guard_owner_listen_unix=listen_unix,
        guard_owner_listen_tcp=listen_tcp,
        guard_owner_addrs=tuple(_hostport(a) for a in get["RV_GUARD_OWNER_ADDRS"].split(",") if a.strip()),
        guard_fleet_workers=int(fleet) if fleet else None,
        worker_index=int(src.get("RV_WORKER_INDEX", "0")),
        defaulted=defaulted,
    )
    if get["RV_EXP_OUTPUT_SCAN"].strip().lower() not in ("on", "off"):
        raise ValueError(f"RV_EXP_OUTPUT_SCAN={get['RV_EXP_OUTPUT_SCAN']!r} (on | off)")
    if s.guard_topology not in ("in_process", "owner", "remote"):
        raise ValueError(f"RV_GUARD_TOPOLOGY={s.guard_topology!r} (in_process | owner | remote)")
    if s.guard_topology == "remote" and not s.guard_owner_addrs:
        raise ValueError("RV_GUARD_TOPOLOGY=remote needs RV_GUARD_OWNER_ADDRS=host:port[,host:port...]")
    if s.guard_fleet_workers is not None and s.guard_fleet_workers < 1:
        raise ValueError("RV_GUARD_FLEET_WORKERS must be >= 1")
    if s.guard_owner_listen_unix and len(os.path.join(s.guard_socket_dir, "guard-99.sock").encode()) > _SUN_PATH_MAX:
        raise ValueError(f"RV_GUARD_SOCKET_DIR={s.guard_socket_dir!r} is too long for a Unix socket path")
    if s.window_overlap >= s.window_tokens - 2:
        raise ValueError("RV_WINDOW_OVERLAP must be smaller than the window payload")
    # a refresh period at (or near) the staleness ceiling makes a fresh snapshot read as stale
    # for the duration of every refresh round trip; require a >= 2x margin, fail fast otherwise
    for name, period in (("RV_KS_REFRESH_MS", s.ks_refresh_ms), ("RV_PLAN_RECONCILE_MS", s.plan_reconcile_ms)):
        if period * 2 > s.ks_stale_ms:
            raise ValueError(f"{name}={period} must be <= RV_KS_STALE_MS/2 ({s.ks_stale_ms / 2})")
    return s


def describe(s: Settings) -> dict[str, object]:
    out: dict[str, object] = {}
    for f in fields(s):
        if f.name == "provider_key":
            out[f.name] = "<set>" if s.provider_key else ""
            continue
        out[f.name] = getattr(s, f.name)
    return out
