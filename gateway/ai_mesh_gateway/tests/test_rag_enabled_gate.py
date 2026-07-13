"""Per-org rag_enabled master toggle must be ENFORCED at the /v1/rag/* request
layer: a disabled org's RAG feature MUST NOT execute (rejected). Previously the
per-org FirewallConfig toggle was displayed in the frontend but ignored — RAG ran
regardless. Fail-open on resolution errors; default enabled.
"""
import types
import main


class _Auth:
    def __init__(self, slug):
        self.org_slug = slug


class _Sync:
    def __init__(self, cfg, raise_=False):
        self.cfg = cfg
        self.raise_ = raise_
    def get_config(self, slug):
        if self.raise_:
            raise RuntimeError("redis down")
        return self.cfg


def test_disabled_org_is_rejected(monkeypatch):
    monkeypatch.setattr(main, "CONFIG_SYNC", _Sync({"rag_enabled": False}))
    assert main._rag_disabled_for_org(_Auth("acme")) is True


def test_enabled_org_allowed(monkeypatch):
    monkeypatch.setattr(main, "CONFIG_SYNC", _Sync({"rag_enabled": True}))
    assert main._rag_disabled_for_org(_Auth("acme")) is False


def test_missing_key_defaults_enabled(monkeypatch):
    monkeypatch.setattr(main, "CONFIG_SYNC", _Sync({}))
    assert main._rag_disabled_for_org(_Auth("acme")) is False


def test_no_org_slug_failopen(monkeypatch):
    monkeypatch.setattr(main, "CONFIG_SYNC", _Sync({"rag_enabled": False}))
    assert main._rag_disabled_for_org(_Auth("")) is False
    assert main._rag_disabled_for_org(_Auth(None)) is False


def test_config_sync_none_failopen(monkeypatch):
    monkeypatch.setattr(main, "CONFIG_SYNC", None)
    assert main._rag_disabled_for_org(_Auth("acme")) is False


def test_config_lookup_error_failopen(monkeypatch):
    monkeypatch.setattr(main, "CONFIG_SYNC", _Sync(None, raise_=True))
    assert main._rag_disabled_for_org(_Auth("acme")) is False


def test_truthy_nonbool_not_treated_as_disabled(monkeypatch):
    # only an explicit False disables (mirrors chat firewall_enabled 'is False').
    monkeypatch.setattr(main, "CONFIG_SYNC", _Sync({"rag_enabled": 0}))
    assert main._rag_disabled_for_org(_Auth("acme")) is False  # 0 is not False by identity
