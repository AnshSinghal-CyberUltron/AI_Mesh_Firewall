"""Helpers for firewall model governance (allowlist + default model)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.models import LLMModelConfig

if TYPE_CHECKING:
    from core.models import Organization


def parse_allowed_models(value: str | list | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return list(dict.fromkeys(str(m).strip() for m in value if str(m).strip()))
    if isinstance(value, str):
        return list(dict.fromkeys(m.strip() for m in value.split(",") if m.strip()))
    return []


def format_allowed_models(names: list[str]) -> str:
    return ", ".join(parse_allowed_models(names))


def get_connected_models(organization: Organization | None) -> list[dict[str, Any]]:
    """Org LLM configs eligible for governance UI (excludes platform guard models)."""
    if organization is None:
        return []
    qs = LLMModelConfig.queryset_user_managed(
        LLMModelConfig.objects.filter(organization=organization).order_by("model_name")
    )
    out: list[dict[str, Any]] = []
    for row in qs:
        out.append(
            {
                "id": row.id,
                "model_name": row.model_name,
                "model_id": row.model_id,
                "provider": row.provider,
                "provider_display": row.get_provider_display(),
                "is_active": row.is_active,
                "api_key_set": row.api_key_set,
            }
        )
    return out


def connected_model_names(organization: Organization | None) -> set[str]:
    return {m["model_name"] for m in get_connected_models(organization)}


def stale_allowed_models(allowed: list[str], organization: Organization | None) -> list[str]:
    names = connected_model_names(organization)
    if not names:
        return list(allowed)
    return [m for m in allowed if m not in names]


def sanitize_allowlist_for_org(
    organization: Organization | None,
    *,
    allowed_models: str | list | None,
    default_model: str | None,
) -> tuple[str, str, bool]:
    """
    Return (allowed_models_csv, default_model, changed) intersected with connected models.
    If no models are connected, allowlist and default are cleared.
    """
    allowed = parse_allowed_models(allowed_models)
    default = (default_model or "").strip()
    connected = connected_model_names(organization)

    if not connected:
        new_allowed = ""
        new_default = ""
    else:
        cleaned = [m for m in allowed if m in connected]
        new_allowed = format_allowed_models(cleaned)
        if default and default in connected and (not cleaned or default in cleaned):
            new_default = default
        elif cleaned:
            new_default = cleaned[0]
        else:
            new_default = ""

    changed = new_allowed != format_allowed_models(allowed) or new_default != default
    return new_allowed, new_default, changed


def validate_governance_fields(
    *,
    organization: Organization | None,
    allowed_models: str | list | None,
    default_model: str | None,
    model_isolation_enabled: bool,
) -> dict[str, str]:
    """
    Return field-level validation errors (empty dict = ok).
    """
    errors: dict[str, str] = {}
    allowed = parse_allowed_models(allowed_models)
    default = (default_model or "").strip()
    connected = connected_model_names(organization)

    if not connected:
        if allowed:
            errors["allowed_models"] = (
                "No models are connected for this organization. "
                "Add models under Model Connection before setting an allowlist."
            )
        if default:
            errors["default_model"] = (
                "No connected models are available. Connect a model before choosing a default."
            )
        return errors

    unknown = [m for m in allowed if m not in connected]
    if unknown:
        errors["allowed_models"] = (
            "These models are not connected to your organization: "
            + ", ".join(unknown)
            + ". Remove them or add them under Model Connection."
        )

    if default and default not in connected:
        errors["default_model"] = (
            f'"{default}" is not a connected model. Choose a model from Model Connection.'
        )

    if model_isolation_enabled and allowed:
        if default and default not in allowed:
            errors["default_model"] = (
                "Default model must be included in the allowed models list when model isolation is enabled."
            )
    elif model_isolation_enabled and not allowed:
        # Empty allowlist with isolation: gateway may block everything — warn via serializer note only
        pass

    return errors
