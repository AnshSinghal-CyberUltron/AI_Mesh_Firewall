from ai_mesh_gateway.bedrock_inference import (
    SIMULATOR_BEDROCK_MODEL_ID,
    bedrock_model_id_candidates,
    build_simulator_bedrock_route_metadata,
    select_simulator_default_model,
)


def test_bedrock_model_id_candidates_include_prefixed_and_unprefixed_variants():
    variants = bedrock_model_id_candidates("openai.gpt-oss-120b-1:0")

    assert "openai.gpt-oss-120b-1:0" in variants
    assert "bedrock/openai.gpt-oss-120b-1:0" in variants


def test_build_simulator_bedrock_route_metadata_is_global_and_boto3_only():
    metadata = build_simulator_bedrock_route_metadata("bedrock-gpt-oss-120b")

    assert metadata["selected_model"] == SIMULATOR_BEDROCK_MODEL_ID
    assert metadata["routed_model"] == SIMULATOR_BEDROCK_MODEL_ID
    assert metadata["decision_source"] == "simulator_bedrock_boto3_global"
    assert "boto3" in metadata["routing_reason"].lower()


def test_select_simulator_default_model_prefers_bedrock_model_name():
    models = [
        {"model_name": "gpt-4o-mini", "model_id": "openai/gpt-4o-mini", "provider": "openai", "is_active": True},
        {
            "model_name": "bedrock-gpt-oss-120b",
            "model_id": "bedrock/openai.gpt-oss-120b-1:0",
            "provider": "bedrock",
            "is_active": True,
        },
    ]

    selected = select_simulator_default_model(models)

    assert selected == "bedrock-gpt-oss-120b"


def test_select_simulator_default_model_prefers_bedrock_model_id_when_name_differs():
    models = [
        {"model_name": "openai-default", "model_id": "openai/gpt-4o-mini", "provider": "openai", "is_active": True},
        {
            "model_name": "prod-bedrock-route",
            "model_id": "bedrock/openai.gpt-oss-120b-1:0",
            "provider": "bedrock",
            "is_active": True,
        },
    ]

    selected = select_simulator_default_model(models)

    assert selected == "prod-bedrock-route"


def test_select_simulator_default_model_returns_empty_when_bedrock_missing():
    models = [
        {"model_name": "gpt-4o-mini", "model_id": "openai/gpt-4o-mini", "provider": "openai", "is_active": True},
        {"model_name": "claude-3-5-sonnet", "model_id": "anthropic/claude-3-5-sonnet", "provider": "anthropic", "is_active": True},
    ]

    selected = select_simulator_default_model(models)

    assert selected == ""
