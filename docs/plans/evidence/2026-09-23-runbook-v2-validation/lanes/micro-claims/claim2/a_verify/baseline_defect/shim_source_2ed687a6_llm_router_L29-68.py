def looks_like_display_alias(model_id: str) -> bool:
    """True when a catalog id is a UI display name, not a LiteLLM/provider id.

    Live Mesh scored ``Haiku`` (Title Case, no provider prefix) and dispatched it.
    LiteLLM 404'd in milliseconds. Aliases with a provider path, version token, or
    hyphenated slug (``google/gemini-2.0-flash``, ``gpt-4o-mini``, ``gemini-flash-cheap``)
    are treated as real routing keys.
    """
    mid = (model_id or "").strip()
    if not mid:
        return True
    if " " in mid:
        return True
    if "/" in mid or "\\" in mid or ":" in mid:
        return False
    if "." in mid:
        return False
    if "-" in mid or any(ch.isdigit() for ch in mid):
        return False
    return bool(mid[:1].isupper() and mid[1:].islower() and mid.isalpha())


def looks_like_provider_model_id(model_id: str) -> bool:
    mid = (model_id or "").strip()
    if not mid:
        return False
    return not looks_like_display_alias(mid)


def catalog_row_is_display_alias(model: dict | None) -> bool:
    """True when a routing row would dispatch a UI alias (``Haiku``) to LiteLLM."""
    if not isinstance(model, dict):
        return False
    name = str(model.get("model_name") or "").strip()
    model_id = str(model.get("model_id") or "").strip()
    upstream = model_id or name
    params = model.get("litellm_params")
    if isinstance(params, dict) and str(params.get("model") or "").strip():
        upstream = str(params.get("model")).strip()
    return looks_like_display_alias(name) or looks_like_display_alias(upstream)
