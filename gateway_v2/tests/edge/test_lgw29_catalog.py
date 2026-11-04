"""GW29 catalog loader: valid files load, invalid inputs refuse to start, the digest is stable."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from gateway_v2.edge.incident_catalog import (
    AUTO_TARGET_ENV,
    CATALOG_ENV,
    FALLBACKS_ENV,
    CatalogError,
    EmptyCatalog,
    InterimConstraintSource,
    load_catalog_config,
    parse_catalog,
)

GOOD = {
    "models": [
        {"name": "GPT-4o", "provider": "openai", "tags": ["pci"], "sensitivity": 2,
         "aliases": ["gpt-4o-2024-08-06"]},
        {"name": "gpt-4o-mini", "provider": "openai", "tags": ["pci", "hipaa"], "sensitivity": 3},
    ],
}


def _file(tmp_path: Path, doc: object) -> dict[str, str]:
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return {CATALOG_ENV: str(path)}


def test_no_inputs_means_no_catalog_and_unknown_constraints() -> None:
    cfg = load_catalog_config({})
    assert cfg.catalog is None and cfg.digest == "" and not cfg.knows("gpt-4o")
    assert cfg.aliases == {} and cfg.auto_target is None
    assert isinstance(cfg.model_catalog, EmptyCatalog)
    constraints = cfg.constraints.for_request(None, "org", "gpt-4o")
    assert constraints.known is False, "no catalog: every reroute is a declared refusal"


def test_a_valid_catalog_canonicalises_names_and_builds_the_alias_table(tmp_path: Path) -> None:
    cfg = load_catalog_config(_file(tmp_path, GOOD))
    assert cfg.catalog is not None
    assert cfg.catalog.names() == ("gpt-4o", "gpt-4o-mini")
    assert dict(cfg.aliases) == {"gpt-4o-2024-08-06": "gpt-4o"}
    profile = cfg.catalog.profile("gpt-4o")
    assert profile is not None and profile.compliance_tags == {"pci"}
    assert profile.sensitivity_ceiling == 2
    assert cfg.constraints.for_request(None, "o", "gpt-4o").known is True
    with pytest.raises(TypeError):
        cfg.aliases["x"] = "y"  # type: ignore[index]


def test_the_digest_is_stable_and_changes_with_the_catalog(tmp_path: Path) -> None:
    first = load_catalog_config(_file(tmp_path, GOOD)).digest
    again = load_catalog_config(_file(tmp_path, GOOD)).digest
    reordered = {"models": list(reversed(GOOD["models"]))}
    assert first == again == load_catalog_config(_file(tmp_path, reordered)).digest
    assert len(first) == 64
    changed = json.loads(json.dumps(GOOD))
    changed["models"][0]["sensitivity"] = 9
    assert load_catalog_config(_file(tmp_path, changed)).digest != first


@pytest.mark.parametrize("doc", [
    [],
    {},
    {"models": []},
    {"models": ["x"]},
    {"models": [{"provider": "p"}]},
    {"models": [{"name": "bad name", "provider": "p"}]},
    {"models": [{"name": "m", "provider": ""}]},
    {"models": [{"name": "m", "provider": "p", "tags": "pci"}]},
    {"models": [{"name": "m", "provider": "p", "tags": ["bad tag!"]}]},
    {"models": [{"name": "m", "provider": "p", "sensitivity": -1}]},
    {"models": [{"name": "m", "provider": "p", "sensitivity": True}]},
    {"models": [{"name": "m", "provider": "p", "aliases": "a"}]},
    {"models": [{"name": "m", "provider": "p"}, {"name": "M", "provider": "p"}]},
    {"models": [{"name": "m", "provider": "p", "aliases": ["n"]},
                {"name": "n", "provider": "p"}]},
    {"models": [{"name": "m", "provider": "p", "aliases": ["z"]},
                {"name": "n", "provider": "p", "aliases": ["z"]}]},
])
def test_an_invalid_catalog_refuses_to_start(doc: object) -> None:
    with pytest.raises(CatalogError):
        parse_catalog(doc)


def test_unreadable_or_non_json_catalog_files_refuse(tmp_path: Path) -> None:
    with pytest.raises(CatalogError):
        load_catalog_config({CATALOG_ENV: str(tmp_path / "missing.json")})
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(CatalogError):
        load_catalog_config({CATALOG_ENV: str(bad)})


def test_fallbacks_are_validated_against_the_catalog(tmp_path: Path) -> None:
    env = _file(tmp_path, GOOD)
    cfg = load_catalog_config({**env, FALLBACKS_ENV: "GPT-4o-2024-08-06 = gpt-4o-mini"})
    assert cfg.fallbacks.fallback_for("gpt-4o") == "gpt-4o-mini"
    assert cfg.fallbacks.fallback_for("gpt-4o-mini") is None
    for bad in ("gpt-4o=ghost", "ghost=gpt-4o", "gpt-4o=gpt-4o", "gpt-4o", "gpt-4o=gpt-4o-mini,"
                "gpt-4o=gpt-4o-mini", "a b=gpt-4o"):
        with pytest.raises(CatalogError):
            load_catalog_config({**env, FALLBACKS_ENV: bad})


def test_fallbacks_without_a_catalog_are_accepted_but_stay_unusable() -> None:
    cfg = load_catalog_config({FALLBACKS_ENV: "a=b"})
    assert cfg.fallbacks.fallback_for("a") == "b"
    assert cfg.constraints.for_request(None, "o", "a").known is False
    assert cfg.fallbacks.models() == {"a", "b"}


def test_the_auto_target_must_be_a_catalog_model(tmp_path: Path) -> None:
    env = _file(tmp_path, GOOD)
    assert load_catalog_config({**env, AUTO_TARGET_ENV: "GPT-4o-2024-08-06"}).auto_target == (
        "gpt-4o"
    )
    for bad_env in ({**env, AUTO_TARGET_ENV: "ghost"}, {AUTO_TARGET_ENV: "gpt-4o"},
                    {**env, AUTO_TARGET_ENV: "bad name"}):
        with pytest.raises(CatalogError):
            load_catalog_config(bad_env)


def test_the_interim_source_ignores_the_request() -> None:
    constraints = InterimConstraintSource(known=True).for_request(object(), "o", "m")
    assert constraints.allowed_models is None and constraints.required_tags == frozenset()
    assert constraints.sensitivity_rank == 0
