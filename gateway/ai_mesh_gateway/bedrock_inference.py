"""Bedrock model selection helpers (used by gateway routing and unit tests)."""

from __future__ import annotations

import os
from typing import Any

SIMULATOR_PREFERRED_MODEL_NAME = "bedrock-gpt-oss-120b"
SIMULATOR_BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL", "openai.gpt-oss-120b-1:0").strip()


def bedrock_model_id_candidates(model_id: str) -> set[str]:
    normalized = (model_id or "").strip().lower()
    if not normalized:
        return set()
    with_prefix = normalized if normalized.startswith("bedrock/") else f"bedrock/{normalized}"
    without_prefix = normalized.replace("bedrock/", "", 1) if normalized.startswith("bedrock/") else normalized
    return {normalized, with_prefix, without_prefix}


SIMULATOR_PREFERRED_MODEL_IDS = bedrock_model_id_candidates(SIMULATOR_BEDROCK_MODEL_ID) | {
    "openai.gpt-oss-120b-1:0",
    "bedrock/openai.gpt-oss-120b-1:0",
}


def build_simulator_bedrock_route_metadata(original_model: str | None) -> dict[str, Any]:
    return {
        "original_model": original_model or "auto",
        "selected_model": SIMULATOR_BEDROCK_MODEL_ID,
        "routed_model": SIMULATOR_BEDROCK_MODEL_ID,
        "decision_source": "simulator_bedrock_boto3_global",
        "routing_reason": (
            "Inference is pinned to global Bedrock model "
            f"{SIMULATOR_BEDROCK_MODEL_ID} via boto3."
        ),
        "policy_summary": "Org model routing is bypassed for direct Bedrock invoke.",
        "decision_factors": ["global_bedrock_credentials", "boto3_direct_invoke"],
        "weights": {},
    }


def select_simulator_default_model(inference_models: list[dict[str, Any]]) -> str:
    if not inference_models:
        return ""

    for model in inference_models:
        model_name = str(model.get("model_name") or "").strip()
        model_id = str(model.get("model_id") or "").strip().lower()
        provider = str(model.get("provider") or "").strip().lower()
        if model_id in SIMULATOR_PREFERRED_MODEL_IDS and (provider == "bedrock" or model_id.startswith("bedrock/")):
            return model_name or str(model.get("model_id") or "").strip()

    for model in inference_models:
        model_name = str(model.get("model_name") or "").strip()
        model_id = str(model.get("model_id") or "").strip().lower()
        provider = str(model.get("provider") or "").strip().lower()
        if model_name.lower() == SIMULATOR_PREFERRED_MODEL_NAME and (provider == "bedrock" or model_id.startswith("bedrock/")):
            return model_name

    return ""
