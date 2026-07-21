"""A-03 LOCK — tenancy must be derived from the catalogue being PROTECTED.

`_catalogue_spans_multiple_orgs` originally counted `CONFIG_SYNC._model_routing_by_org`
while guarding `LLM_ROUTER`'s merged deployment list. Those are populated on DIFFERENT
conditions in the same loop (config_sync.py:578-595): a tenant whose payload carries a
malformed "routing" section skips the per-org registration, but its deployments still
reach the shared router unconditionally. The detector counted one tenant while the
router held two, so an org-less key enumerated the merged cross-org catalogue.

The fix reads the H7 `_zs_org` tag stamped on every deployment that reaches the router.
Note the tag does NOT survive `get_model_list()` — that builds a fresh OpenAI-shaped
projection ({id, object, owned_by, model_id}) — so a rig that stubs `get_model_list`
cannot exercise this signal at all. With only that projection, "one org with two models"
and "two orgs with one each" are genuinely indistinguishable. Hence these tests model
the RAW router entries, which is what production actually holds.
"""
from unittest.mock import MagicMock
from ai_mesh_gateway import main as gm


def _probe(monkeypatch, by_org, raw_entries):
    cs = MagicMock(); cs._model_routing_by_org = dict(by_org)
    router = MagicMock(); router._router = MagicMock()
    router._router.model_list = raw_entries
    monkeypatch.setattr(gm, "CONFIG_SYNC", cs)
    monkeypatch.setattr(gm, "LLM_ROUTER", router)
    return gm._catalogue_spans_multiple_orgs()


def test_a03_tags_reveal_the_hidden_second_tenant(monkeypatch):
    """The attack: by_org records ONE org (globex's routing section was malformed),
    but globex's deployments still reached the shared router carrying their tag."""
    raw = [{"model_name": "acme-gpt", "_zs_org": "acme"},
           {"model_name": "globex-secret-gpt", "_zs_org": "globex"}]
    assert _probe(monkeypatch, {"acme": [1]}, raw) is True, \
        "hidden second tenant not detected from _zs_org tags"


def test_a03_genuine_single_tenant_still_serves(monkeypatch):
    """Inverse risk: one org with several models must NOT be read as multi-tenant."""
    raw = [{"model_name": "acme-gpt", "_zs_org": "acme"},
           {"model_name": "acme-fast", "_zs_org": "acme"}]
    assert _probe(monkeypatch, {"acme": [1]}, raw) is False, \
        "single tenant misread as multi-tenant — model discovery would break"
