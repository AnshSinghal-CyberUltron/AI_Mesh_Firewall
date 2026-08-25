"""Phase 0 Prometheus honesty: multiprocess scrape + T_addon histograms.

When PROMETHEUS_MULTIPROC_DIR is unset, the dedicated CollectorRegistry
behavior in test_metrics_endpoint.py must stay unchanged.

When the env is set, /metrics must aggregate across gunicorn workers
(RC-8: dedicated-registry scrape under-reports × WEB_CONCURRENCY).
"""
from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest
from prometheus_client import REGISTRY as DEFAULT_REGISTRY


GATEWAY_ROOT = Path(__file__).resolve().parents[2]


def _wipe_default_registry() -> None:
    """Avoid DuplicatedTimeseries when reloading metrics under MP mode."""
    collectors = list(getattr(DEFAULT_REGISTRY, "_collector_to_names", {}))
    for collector in collectors:
        try:
            DEFAULT_REGISTRY.unregister(collector)
        except Exception:
            pass


def _reload_metrics():
    import ai_mesh_gateway.metrics as m

    _wipe_default_registry()
    return importlib.reload(m)


def _counter_value(text: str, name: str, labels: dict[str, str]) -> float | None:
    """Read a counter sample; Prometheus may emit labels in any order."""
    prefix = name + "{"
    for line in text.splitlines():
        if not line.startswith(prefix):
            continue
        head, _, rest = line.partition("{")
        if head != name:
            continue
        label_part, _, value_part = rest.partition("}")
        parsed: dict[str, str] = {}
        for item in label_part.split(","):
            if "=" not in item:
                continue
            key, raw = item.split("=", 1)
            parsed[key.strip()] = raw.strip().strip('"')
        if parsed == labels:
            return float(value_part.strip().split()[0])
    return None


def _hist_sum(text: str, name: str, org: str = "acme") -> float | None:
    needle = f'{name}_sum{{org="{org}"}}'
    for line in text.splitlines():
        if line.startswith(needle):
            return float(line.split()[-1])
    return None


@pytest.fixture()
def metrics_module():
    """Process-local registry (no MP dir) — same contract as test_metrics_endpoint."""
    os.environ.pop("PROMETHEUS_MULTIPROC_DIR", None)
    os.environ.pop("prometheus_multiproc_dir", None)
    return _reload_metrics()


def test_addon_histograms_observed_from_stage_metrics(metrics_module):
    metrics_module.record_chat_completion(
        "acme",
        "success",
        1.25,
        {
            "auth_ms": 2.0,
            "t_addon_pre_ms": 12.0,
            "t_addon_post_ms": 4.0,
            "t_t2_ms": 8.0,
        },
    )
    text = metrics_module.render_latest()[0].decode("utf-8")
    assert "amf_gateway_t_addon_pre_seconds" in text
    assert "amf_gateway_t_addon_post_seconds" in text
    assert "amf_gateway_t_t2_seconds" in text
    assert _hist_sum(text, "amf_gateway_t_addon_pre_seconds") == pytest.approx(0.012)
    assert _hist_sum(text, "amf_gateway_t_addon_post_seconds") == pytest.approx(0.004)
    assert _hist_sum(text, "amf_gateway_t_t2_seconds") == pytest.approx(0.008)
    # Existing stage histogram still recorded.
    assert 'stage="auth"' in text


def test_addon_histograms_computed_when_keys_missing(metrics_module):
    metrics_module.record_chat_completion(
        "acme",
        "success",
        1.0,
        {
            "model_output_ms": 400.0,
            "output_guardrail_ms": 50.0,
            "tier2_ms": 80.0,
        },
    )
    text = metrics_module.render_latest()[0].decode("utf-8")
    # compute_addon_split: pre = 1000 - 400 - 50 = 550 ms
    assert _hist_sum(text, "amf_gateway_t_addon_pre_seconds") == pytest.approx(0.550, abs=0.001)
    assert _hist_sum(text, "amf_gateway_t_addon_post_seconds") == pytest.approx(0.050, abs=0.001)
    assert _hist_sum(text, "amf_gateway_t_t2_seconds") == pytest.approx(0.080, abs=0.001)


def test_capacity_fail_counter_increments_when_stub_llm_env_on(metrics_module, monkeypatch):
    monkeypatch.setenv("GATEWAY_LOADTEST_STUB_LLM", "1")
    metrics_module.record_chat_completion("acme", "success", 0.01, {"auth_ms": 1.0})
    text = metrics_module.render_latest()[0].decode("utf-8")
    assert "amf_gateway_capacity_fail_total" in text
    assert 'reason="stub_llm"' in text
    val = _counter_value(
        text, "amf_gateway_capacity_fail_total", {"reason": "stub_llm"}
    )
    assert val == pytest.approx(1.0)


def test_stub_completion_increments_capacity_fail(metrics_module, monkeypatch):
    monkeypatch.setenv("GATEWAY_LOADTEST_STUB_LLM", "1")
    from ai_mesh_gateway.llm_router import loadtest_stub_completion

    loadtest_stub_completion({"model": "gpt-4o-mini"})
    text = metrics_module.render_latest()[0].decode("utf-8")
    val = _counter_value(
        text, "amf_gateway_capacity_fail_total", {"reason": "stub_llm"}
    )
    assert val is not None and val >= 1.0


def _record_request_in_fresh_process(mp_dir: Path) -> None:
    """prometheus_client picks MultiProcessValue at first import; pytest already imported it."""
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import os, sys\n"
                f"os.environ['PROMETHEUS_MULTIPROC_DIR'] = {str(mp_dir)!r}\n"
                f"sys.path[:0] = {[str(GATEWAY_ROOT), str(GATEWAY_ROOT.parent / 'shared')]!r}\n"
                "from prometheus_client import REGISTRY\n"
                "for c in list(getattr(REGISTRY, '_collector_to_names', {})):\n"
                "    try:\n"
                "        REGISTRY.unregister(c)\n"
                "    except Exception:\n"
                "        pass\n"
                "from ai_mesh_gateway.metrics import record_request\n"
                "record_request('acme', 'allowed', 0.05)\n"
            ),
        ],
        cwd=str(GATEWAY_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert child.returncode == 0, child.stdout + child.stderr


def test_multiprocess_render_aggregates_two_pids(tmp_path, monkeypatch):
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path))
    _wipe_default_registry()
    _record_request_in_fresh_process(tmp_path)
    _record_request_in_fresh_process(tmp_path)

    m = _reload_metrics()
    text = m.render_latest()[0].decode("utf-8")
    val = _counter_value(
        text, "amf_gateway_requests_total", {"org": "acme", "decision": "allowed"}
    )
    assert val == pytest.approx(2.0), text


def test_gauge_uses_livesum_when_multiprocess_dir_set(tmp_path, monkeypatch):
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path))
    m = _reload_metrics()
    assert m.gateway_active_connections._multiprocess_mode == "livesum"
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import os, sys\n"
                f"os.environ['PROMETHEUS_MULTIPROC_DIR'] = {str(tmp_path)!r}\n"
                f"sys.path[:0] = {[str(GATEWAY_ROOT), str(GATEWAY_ROOT.parent / 'shared')]!r}\n"
                "from prometheus_client import REGISTRY\n"
                "for c in list(getattr(REGISTRY, '_collector_to_names', {})):\n"
                "    try:\n"
                "        REGISTRY.unregister(c)\n"
                "    except Exception:\n"
                "        pass\n"
                "from ai_mesh_gateway.metrics import inc_active_connections\n"
                "inc_active_connections(1)\n"
            ),
        ],
        cwd=str(GATEWAY_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert child.returncode == 0, child.stdout + child.stderr
    text = m.render_latest()[0].decode("utf-8")
    assert "amf_gateway_active_connections" in text


def test_gunicorn_conf_marks_dead_workers():
    conf = (GATEWAY_ROOT / "gunicorn.conf.py").read_text(encoding="utf-8")
    assert "mark_process_dead" in conf
    entry = (GATEWAY_ROOT / "entrypoint.sh").read_text(encoding="utf-8")
    assert "gunicorn.conf.py" in entry
