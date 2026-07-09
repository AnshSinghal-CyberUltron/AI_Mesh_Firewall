"""#42 regression: a failed enforcement-notification broadcast must be LOGGED,
not silently dropped, while still not propagating (resilient delivery).

The old bare `except Exception: return` swallowed broadcast failures with zero
operator visibility — a broken channel layer would drop every real-time
enforcement alert invisibly. Runs in the control test env (needs channels).
Behavior additionally proven standalone this session via AST extraction of the
function bodies (drop logged, no propagation, unscoped stays fail-closed).
"""

from unittest import mock

from django.test import SimpleTestCase

from ws import notify


class NotifyLoggingTests(SimpleTestCase):
    @mock.patch("ws.notify.get_channel_layer")
    @mock.patch("ws.notify.async_to_sync")
    def test_broadcast_failure_is_logged_and_not_propagated(self, m_ats, m_gcl):
        m_gcl.return_value = object()  # truthy channel layer
        m_ats.return_value = mock.Mock(side_effect=RuntimeError("channel layer down"))
        with self.assertLogs("ws.notify", level="WARNING") as cm:
            # must not raise
            notify.send_enforcement_notification({"organization_id": 7, "action": "block"})
        self.assertTrue(
            any("failed to broadcast enforcement notification" in m for m in cm.output),
            cm.output,
        )

    @mock.patch("ws.notify.get_channel_layer")
    @mock.patch("ws.notify.async_to_sync")
    def test_unscoped_payload_fail_closed_no_broadcast(self, m_ats, m_gcl):
        m_gcl.return_value = object()
        # no organization_id anywhere → tenant-isolation fail-closed, no broadcast
        notify.send_enforcement_notification({"action": "block"})
        m_ats.assert_not_called()
