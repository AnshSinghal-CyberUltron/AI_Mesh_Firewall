"""GW29 model catalog, fallback map and interim constraint source (design 4.5, 4.6).

Three operator inputs, all read ONCE at boot, validated, and immutable afterwards:

* ``AMF_MODEL_CATALOG`` -- a JSON file ``{"models": [{"name", "provider", "tags", "sensitivity",
  "aliases"}]}``. It gives the alias table the kill-switch parser and the gate share, and the
  compliance/sensitivity profile every reroute is checked against. An invalid file refuses to
  start (:class:`CatalogError`); the production profile re-raises it as ``ProfileError``.
* ``AMF_MODEL_FALLBACKS`` -- inline ``model=fallback`` pairs, comma separated. With a catalog, both
  sides must be catalog models. A fallback is a hint for the reroute; it is checked on every use.
* ``AMF_MODEL_AUTO_TARGET`` -- the catalog model the ``auto`` routing sentinel resolves to. Unset
  means ``auto`` is refused. It must name a catalog model.

With NO catalog the constraint source reports ``known=False``, so every reroute is a declared
``REROUTE_REFUSED``: GW29 never reroutes on a guess (4.5). :class:`InterimConstraintSource` is
replaced by GW28 behind the ``ConstraintSource`` protocol without touching the gate.

Names are canonicalised with :func:`~gateway_v2.domain.incident.canonical_model` WITHOUT aliases
(the writer's form); aliases are applied at read. Alias chains are rejected, which is what makes
``canonical_model`` idempotent.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from gateway_v2.domain.incident import (
    MODEL_NAME_MAX,
    SLUG,
    FallbackPolicy,
    ModelCatalog,
    ModelProfile,
    RequestConstraints,
    canonical_model,
)

__all__ = (
    "CATALOG_ENV",
    "AUTO_TARGET_ENV",
    "FALLBACKS_ENV",
    "CatalogConfig",
    "CatalogError",
    "EmptyCatalog",
    "FallbackMap",
    "InterimConstraintSource",
    "LoadedCatalog",
    "load_catalog_config",
    "parse_catalog",
    "parse_fallbacks",
)

CATALOG_ENV = "AMF_MODEL_CATALOG"
FALLBACKS_ENV = "AMF_MODEL_FALLBACKS"
AUTO_TARGET_ENV = "AMF_MODEL_AUTO_TARGET"

_NO_ALIASES: Mapping[str, str] = MappingProxyType({})


class CatalogError(ValueError):
    """A catalog, fallback or auto-target input is invalid. The boot refuses to start."""


@dataclass(frozen=True, slots=True)
class LoadedCatalog:
    """The validated catalog: profiles by canonical name plus the alias table."""

    profiles: Mapping[str, ModelProfile]
    aliases: Mapping[str, str]

    def profile(self, model: str) -> ModelProfile | None:
        return self.profiles.get(model)

    def knows(self, model: str) -> bool:
        return model in self.profiles

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self.profiles))

    def digest(self) -> str:
        """sha256 hex of the canonical JSON of the catalog and alias map (design 4.6)."""
        body = {
            "models": [
                {
                    "name": p.name,
                    "provider": p.provider,
                    "tags": sorted(p.compliance_tags),
                    "sensitivity": p.sensitivity_ceiling,
                }
                for p in (self.profiles[name] for name in sorted(self.profiles))
            ],
            "aliases": {alias: self.aliases[alias] for alias in sorted(self.aliases)},
        }
        text = json.dumps(body, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode("ascii")).hexdigest()


class EmptyCatalog:
    """The catalog of a deployment with none configured: it knows no model and has no profile."""

    __slots__ = ()

    def profile(self, model: str) -> ModelProfile | None:
        return None

    def knows(self, model: str) -> bool:
        return False


class FallbackMap:
    """``model -> fallback`` (both canonical). Implements ``FallbackPolicy``."""

    __slots__ = ("_pairs",)

    def __init__(self, pairs: Mapping[str, str] | None = None) -> None:
        self._pairs: Mapping[str, str] = MappingProxyType(dict(pairs or {}))

    def fallback_for(self, model: str) -> str | None:
        return self._pairs.get(model)

    def models(self) -> frozenset[str]:
        """Every model named on either side of a pair (operator-known, so it may be tracked)."""
        return frozenset(self._pairs) | frozenset(self._pairs.values())


class InterimConstraintSource:
    """Interim ``ConstraintSource`` until GW28: known only when a catalog is configured.

    ``allowed_models`` is None, no required tags, sensitivity rank 0. With no catalog the
    constraints are UNKNOWN, which makes every reroute a refusal (4.5 rule 5).
    """

    __slots__ = ("_known",)

    def __init__(self, *, known: bool) -> None:
        self._known = known

    def for_request(self, plan: object, org_id: str, requested_model: str) -> RequestConstraints:
        del plan, org_id, requested_model
        return RequestConstraints(
            known=self._known,
            allowed_models=None,
            required_tags=frozenset(),
            sensitivity_rank=0,
        )


@dataclass(frozen=True, slots=True)
class CatalogConfig:
    """Everything the boot reads from the three inputs. ``catalog`` is None when none is set."""

    catalog: LoadedCatalog | None
    fallbacks: FallbackMap
    auto_target: str | None
    constraints: InterimConstraintSource

    @property
    def aliases(self) -> Mapping[str, str]:
        return _NO_ALIASES if self.catalog is None else self.catalog.aliases

    def knows(self, model: str) -> bool:
        return self.catalog is not None and self.catalog.knows(model)

    @property
    def digest(self) -> str:
        return "" if self.catalog is None else self.catalog.digest()

    @property
    def model_catalog(self) -> ModelCatalog:
        return EmptyCatalog() if self.catalog is None else self.catalog

    @property
    def fallback_policy(self) -> FallbackPolicy:
        return self.fallbacks


def _canon(raw: object, what: str) -> str:
    name = canonical_model(raw, None)
    if name is None:
        raise CatalogError(f"{what} is not a valid model name")
    return name


def _text(entry: Mapping[str, object], key: str, what: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > MODEL_NAME_MAX:
        raise CatalogError(f"{what}: {key!r} must be a short non-empty string")
    return value.strip()


def _tags(entry: Mapping[str, object], what: str) -> frozenset[str]:
    raw = entry.get("tags", [])
    if not isinstance(raw, list):
        raise CatalogError(f"{what}: 'tags' must be a list")
    tags: set[str] = set()
    for tag in raw:
        if not isinstance(tag, str) or SLUG.fullmatch(tag) is None:
            raise CatalogError(f"{what}: invalid tag")
        tags.add(tag.casefold())
    return frozenset(tags)


def _sensitivity(entry: Mapping[str, object], what: str) -> int:
    value = entry.get("sensitivity", 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CatalogError(f"{what}: 'sensitivity' must be a non-negative integer")
    return value


def _entry_aliases(entry: Mapping[str, object], what: str) -> tuple[str, ...]:
    raw = entry.get("aliases", [])
    if not isinstance(raw, list):
        raise CatalogError(f"{what}: 'aliases' must be a list")
    return tuple(_canon(alias, f"{what}: alias") for alias in raw)


def parse_catalog(raw: object) -> LoadedCatalog:
    """Validate a decoded catalog document. Raises :class:`CatalogError`; never half-loads."""
    models = raw.get("models") if isinstance(raw, dict) else None
    if not isinstance(models, list) or not models:
        raise CatalogError("catalog needs a non-empty 'models' list")
    profiles: dict[str, ModelProfile] = {}
    pending: list[tuple[str, str]] = []
    for index, entry in enumerate(models):
        what = f"catalog model #{index}"
        if not isinstance(entry, dict):
            raise CatalogError(f"{what} must be an object")
        name = _canon(entry.get("name"), what)
        if name in profiles:
            raise CatalogError(f"{what}: duplicate model name")
        profiles[name] = ModelProfile(
            name=name,
            provider=_text(entry, "provider", what),
            compliance_tags=_tags(entry, what),
            sensitivity_ceiling=_sensitivity(entry, what),
        )
        pending.extend((alias, name) for alias in _entry_aliases(entry, what))
    aliases: dict[str, str] = {}
    for alias, target in pending:
        if alias in profiles:
            raise CatalogError("an alias may not shadow a catalog model name")
        if alias in aliases:
            raise CatalogError("an alias may name only one model")
        aliases[alias] = target
    return LoadedCatalog(MappingProxyType(profiles), MappingProxyType(aliases))


def parse_fallbacks(
    raw: str, catalog: LoadedCatalog | None, aliases: Mapping[str, str],
) -> FallbackMap:
    """``a=b,c=d`` -> a :class:`FallbackMap`. With a catalog both sides must be catalog models."""
    pairs: dict[str, str] = {}
    for item in (part.strip() for part in raw.split(",")):
        if not item:
            continue
        left, sep, right = item.partition("=")
        if not sep:
            raise CatalogError(f"{FALLBACKS_ENV} entry needs 'model=fallback'")
        model = canonical_model(left, aliases)
        fallback = canonical_model(right, aliases)
        if model is None or fallback is None:
            raise CatalogError(f"{FALLBACKS_ENV} names an invalid model")
        if model == fallback:
            raise CatalogError(f"{FALLBACKS_ENV}: a model cannot fall back to itself")
        if model in pairs:
            raise CatalogError(f"{FALLBACKS_ENV}: a model has one fallback")
        if catalog is not None and not (catalog.knows(model) and catalog.knows(fallback)):
            raise CatalogError(f"{FALLBACKS_ENV} names a model that is not in the catalog")
        pairs[model] = fallback
    return FallbackMap(pairs)


def _read_catalog(path: str) -> LoadedCatalog:
    try:
        text = Path(path).read_text(encoding="utf-8")
        return parse_catalog(json.loads(text))
    except (OSError, UnicodeDecodeError) as exc:
        raise CatalogError(f"{CATALOG_ENV} cannot be read: {type(exc).__name__}") from exc
    except json.JSONDecodeError as exc:
        raise CatalogError(f"{CATALOG_ENV} is not valid JSON") from exc


def load_catalog_config(env: Mapping[str, str]) -> CatalogConfig:
    """Read the three inputs. Blank/unset = not configured; anything invalid refuses to start."""
    path = (env.get(CATALOG_ENV) or "").strip()
    catalog = _read_catalog(path) if path else None
    aliases = _NO_ALIASES if catalog is None else catalog.aliases
    fallbacks = parse_fallbacks(env.get(FALLBACKS_ENV) or "", catalog, aliases)
    target_raw = (env.get(AUTO_TARGET_ENV) or "").strip()
    auto_target: str | None = None
    if target_raw:
        auto_target = canonical_model(target_raw, aliases)
        if auto_target is None or catalog is None or not catalog.knows(auto_target):
            raise CatalogError(f"{AUTO_TARGET_ENV} must name a model in the catalog")
    return CatalogConfig(
        catalog=catalog,
        fallbacks=fallbacks,
        auto_target=auto_target,
        constraints=InterimConstraintSource(known=catalog is not None),
    )
