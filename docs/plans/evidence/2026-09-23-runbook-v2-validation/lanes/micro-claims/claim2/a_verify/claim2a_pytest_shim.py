"""pytest plugin (-p claim2a_pytest_shim): validates harness profile P1 by running the baseline's own
tests/test_openai_sdk_compat.py under the SAME conditions as claim2a_emit_count.py:
  * socket guard (any AF_INET/AF_INET6 connect/getaddrinfo BLOCKED + reported; live Redis on :6379
    must never be reachable), GATEWAY_REDIS_URL -> *.invalid, AWS disabled via env;
  * the runtime shim for the baseline 2a657fad import defect (llm_router.catalog_row_is_display_alias
    missing) unless CLAIM2A_NO_SHIM=1. Baseline files are never modified."""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import claim2a_lib as L  # noqa: E402

os.environ.update({"GATEWAY_REDIS_URL": L.REDIS_URL_SENTINEL, "AWS_EC2_METADATA_DISABLED": "true",
                   "AWS_SHARED_CREDENTIALS_FILE": "/dev/null", "AWS_CONFIG_FILE": "/dev/null",
                   "AWS_DEFAULT_REGION": "ap-south-1", "LITELLM_LOCAL_MODEL_COST_MAP": "True",
                   "PYTHON_DOTENV_DISABLED": "1",  # stop litellm's import-time load_dotenv() (see harness)
                   "BEDROCK_LOG_DIR": str(HERE / "scratch" / "pytest_bedrock_logdir")})
L._install_socket_guard()


def pytest_configure(config):
    if os.environ.get("CLAIM2A_NO_SHIM") == "1":
        print("[claim2a] shim NOT applied (baseline as-is)", file=sys.stderr)
        return
    for line in L._apply_baseline_import_shim():
        print(f"[claim2a] shim: {line}", file=sys.stderr)


def pytest_sessionfinish(session, exitstatus):
    print(f"\n[claim2a] blocked network attempts: {len(L.BLOCKED)}", file=sys.stderr)
    for b in L.BLOCKED:
        print(f"[claim2a]   {b['op']} {b['target']} -> {L.classify_blocked(b)[:60]}", file=sys.stderr)
