"""Regression: mcp-remote discovery stderr must not trip needs_reauth when token injected."""

from mcp_stdio_adapter import _looks_like_oauth_prompt


def test_mcp_remote_discovery_stderr_not_interactive_when_token_injected():
    line = "[57] Discovered authorization server: https://mcp.linear.app"
    assert not _looks_like_oauth_prompt(line, oauth_header_injected=True)


def test_real_interactive_prompt_still_detected():
    line = "Please visit https://example.com/oauth to authorize this app"
    assert _looks_like_oauth_prompt(line, oauth_header_injected=False)
