"""Unit tests for Microsoft Graph security alert mail helper."""

from unittest.mock import MagicMock, patch

import pytest

from core.graph_mail import graph_mail_configured, send_graph_mail
from core.tasks import deliver_critical_alert_email


@pytest.mark.parametrize(
    "env",
    [
        {"TENANT_ID": "t", "CLIENT_ID": "c", "CLIENT_SECRET": "s"},
        {"TENANT_ID": "", "CLIENT_ID": "c", "CLIENT_SECRET": "s"},
    ],
)
def test_graph_mail_configured(monkeypatch, env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    expected = bool(env["TENANT_ID"] and env["CLIENT_ID"] and env["CLIENT_SECRET"])
    assert graph_mail_configured() is expected


@patch("core.graph_mail.requests.post")
@patch("core.graph_mail.acquire_graph_access_token", return_value="token-abc")
def test_send_graph_mail_accepts_202(mock_token, mock_post):
    mock_resp = MagicMock()
    mock_resp.status_code = 202
    mock_post.return_value = mock_resp

    assert send_graph_mail(
        recipients=["security@example.com"],
        subject="test",
        body_text="body",
    )
    mock_post.assert_called_once()
    payload = mock_post.call_args.kwargs["json"]
    assert payload["message"]["subject"] == "test"
    assert payload["message"]["toRecipients"][0]["emailAddress"]["address"] == "security@example.com"


@patch("core.graph_mail.send_graph_mail", return_value=True)
def test_deliver_critical_alert_uses_graph(mock_send, monkeypatch):
    monkeypatch.setenv("TENANT_ID", "tenant")
    monkeypatch.setenv("CLIENT_ID", "client")
    monkeypatch.setenv("CLIENT_SECRET", "secret")

    ok = deliver_critical_alert_email(
        recipients_str="a@example.com, b@example.com",
        threat_type="prompt_injection",
        risk_score=0.95,
        detail="blocked",
        event_type="critical_alert",
    )
    assert ok is True
    mock_send.assert_called_once()
    recipients = mock_send.call_args.kwargs["recipients"]
    assert list(recipients) == ["a@example.com", "b@example.com"]
