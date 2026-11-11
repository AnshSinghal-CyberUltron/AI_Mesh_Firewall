"""GW17 MCP channels: enumerated, scanned, and in their own ``mcp:`` scope (C21).

Validates: Requirements 4.1, 4.2, 5.1, 5.2, 5.3
"""

from __future__ import annotations

from gateway_v2.domain.channels import TEXT_CHANNELS, unscanned_channels

_MCP_CHANNELS = {
    "mcp_arguments": "mcp:params.arguments",
    "mcp_completion": "mcp:params.argument.value",
    "mcp_result": "mcp:result",
    "mcp_error": "mcp:error",
}


def test_mcp_channels_present_and_scanned() -> None:
    by_name = {channel.name: channel for channel in TEXT_CHANNELS}
    for name, path in _MCP_CHANNELS.items():
        assert name in by_name, f"{name} missing from TEXT_CHANNELS"
        assert by_name[name].sdk_path == path
        assert by_name[name].scanned is True


def test_mcp_scope_is_disjoint_from_chat_output_and_misc() -> None:
    for channel in TEXT_CHANNELS:
        if channel.sdk_path.startswith("mcp:"):
            assert channel.name in _MCP_CHANNELS, channel.name
        else:
            assert channel.name not in _MCP_CHANNELS, channel.name


def test_unscanned_channels_stays_empty() -> None:
    assert unscanned_channels() == ()
