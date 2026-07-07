"""ZeroShield demo — configuration from environment."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

_DEMO_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _DEMO_ROOT.parent.parent
_DEBUG_LOG = _REPO_ROOT / ".cursor" / "debug-618b22.log"


def _debug_log(*, location: str, message: str, data: dict, hypothesis_id: str) -> None:
    # #region agent log
    try:
        _DEBUG_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _DEBUG_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "sessionId": "618b22",
                "timestamp": int(time.time() * 1000),
                "location": location,
                "message": message,
                "data": data,
                "hypothesisId": hypothesis_id,
            }) + "\n")
    except OSError:
        pass
    # #endregion


def _load_dotenv() -> None:
    """Load demo/.env into os.environ (does not override existing vars)."""
    env_path = _DEMO_ROOT / ".env"
    if not env_path.is_file():
        _debug_log(
            location="config.py:_load_dotenv",
            message="no .env file",
            data={"path": str(env_path)},
            hypothesis_id="H1",
        )
        return
    loaded = []
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val
            if key != "ZEROSHIELD_API_KEY":
                loaded.append(key)
            else:
                loaded.append("ZEROSHIELD_API_KEY=(set)")
    _debug_log(
        location="config.py:_load_dotenv",
        message="dotenv loaded",
        data={"keys": loaded},
        hypothesis_id="H1",
    )


_load_dotenv()


def env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


API_KEY = env("ZEROSHIELD_API_KEY")
BASE_URL = env("ZEROSHIELD_BASE_URL", "http://127.0.0.1:8300/v1").rstrip("/")
# Control service used to authenticate the superuser gate (NOT the gateway).
# In-docker this is http://control:8000; local dev defaults to the host port.
CONTROL_BASE_URL = env("CONTROL_BASE_URL", "http://127.0.0.1:8100").rstrip("/")
TIMEOUT = float(env("ZEROSHIELD_TIMEOUT", "120"))
MAX_RETRIES = int(env("ZEROSHIELD_MAX_RETRIES", "3"))
# Streaming responses can exceed non-stream request latency; keep a dedicated
# upper bound to avoid false timeout errors on first-token delays.
STREAM_TIMEOUT = float(env("DEMO_STREAM_TIMEOUT", "180"))
RAG_COLLECTION = env("RAG_DEFAULT_COLLECTION", "demo_knowledge")
DEMO_HOST = env("DEMO_HOST", "127.0.0.1")
DEMO_PORT = int(env("DEMO_PORT", "8765"))
DEFAULT_MODEL = env("DEMO_DEFAULT_MODEL", "gpt-5.2")
# Lite readiness lists models only (fast). Set DEMO_READINESS_LITE=0 for
# deep upstream probe checks.
READINESS_LITE = env("DEMO_READINESS_LITE", "1").lower() not in ("0", "false", "no")
READINESS_PROBE_TIMEOUT = float(env("DEMO_READINESS_PROBE_TIMEOUT", "12"))

_debug_log(
    location="config.py:init",
    message="config resolved",
    data={"has_api_key": bool(API_KEY), "base_url": BASE_URL, "rag_collection": RAG_COLLECTION},
    hypothesis_id="H1",
)
