"""GW16 misc input channels: embeddings/moderations ``input`` enumeration (C21).

Validates: Requirements 3.1, 3.3
"""

from __future__ import annotations

from gateway_v2.domain.channels import TEXT_CHANNELS, unscanned_channels

_MISC_INPUT_NAMES = ("embeddings_input", "moderations_input")


def test_misc_input_channels_present_and_scanned() -> None:
    """R3.1: both misc input channels are enumerated in TEXT_CHANNELS with scanned=True."""
    by_name = {channel.name: channel for channel in TEXT_CHANNELS}
    for name in _MISC_INPUT_NAMES:
        assert name in by_name, f"{name} missing from TEXT_CHANNELS"
        assert by_name[name].scanned is True


def test_misc_input_sdk_paths_are_a_disjoint_scope() -> None:
    """R3.1: sdk_path starts with neither the chat (messages[]) nor output (choices[]) scope."""
    by_name = {channel.name: channel for channel in TEXT_CHANNELS}
    for name in _MISC_INPUT_NAMES:
        sdk_path = by_name[name].sdk_path
        assert not sdk_path.startswith("messages[]"), sdk_path
        assert not sdk_path.startswith("choices[]"), sdk_path


def test_unscanned_channels_stays_empty() -> None:
    """R3.3: every TEXT_CHANNELS entry is scanned, so unscanned_channels() is empty."""
    assert unscanned_channels() == ()
