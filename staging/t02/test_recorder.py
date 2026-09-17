#!/usr/bin/env python3
"""Hermetic tests for the T02 recorder (no Docker)."""
from __future__ import annotations

import json
import os
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

os.environ["T02_RECORDER_KEY"] = "rec-test-key-aaaa"
os.environ["T02_ADMIN_TOKEN"] = "adm-test-token-bbbb"

from recorder import AdminHandler, OpenAIHandler, _calls, _fault, _lock  # noqa: E402


def _free_port() -> int:
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class RecorderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.openai_port = _free_port()
        cls.admin_port = _free_port()
        cls.openai = ThreadingHTTPServer(("127.0.0.1", cls.openai_port), OpenAIHandler)
        cls.admin = ThreadingHTTPServer(("127.0.0.1", cls.admin_port), AdminHandler)
        cls.ot = threading.Thread(target=cls.openai.serve_forever, daemon=True)
        cls.at = threading.Thread(target=cls.admin.serve_forever, daemon=True)
        cls.ot.start()
        cls.at.start()
        time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        cls.openai.shutdown()
        cls.admin.shutdown()

    def setUp(self):
        with _lock:
            _calls.clear()
            _fault.update({"mode": "none", "delay_ms": 0, "status": 200})

    def _http(self, method, url, *, headers=None, body=None, timeout=5):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                return resp.status, json.loads(raw.decode() or "null") if raw else None
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                parsed = json.loads(raw.decode() or "null")
            except Exception:
                parsed = {"_raw": raw[:200].decode("utf-8", "replace")}
            return exc.code, parsed

    def test_health(self):
        st, body = self._http("GET", f"http://127.0.0.1:{self.openai_port}/health")
        self.assertEqual(st, 200)
        self.assertEqual(body["status"], "ok")

    def test_unauthorized_openai(self):
        st, _ = self._http(
            "POST",
            f"http://127.0.0.1:{self.openai_port}/v1/chat/completions",
            body={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
        )
        self.assertEqual(st, 401)

    def test_allow_records_original(self):
        prompt = "Reply with pong. nonce=allow-1"
        st, body = self._http(
            "POST",
            f"http://127.0.0.1:{self.openai_port}/v1/chat/completions",
            headers={"Authorization": "Bearer rec-test-key-aaaa"},
            body={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": prompt}]},
        )
        self.assertEqual(st, 200)
        self.assertIn("pong-t02", body["choices"][0]["message"]["content"])
        ast, calls = self._http(
            "GET",
            f"http://127.0.0.1:{self.admin_port}/calls",
            headers={"X-T02-Admin": "adm-test-token-bbbb"},
        )
        self.assertEqual(ast, 200)
        self.assertEqual(calls["count"], 1)
        self.assertEqual(calls["calls"][0]["prompt"], prompt)
        self.assertFalse(calls["calls"][0]["contains_ssn"])

    def test_ssn_flagged_when_raw_arrives(self):
        prompt = "SSN 123-45-6789"
        st, _ = self._http(
            "POST",
            f"http://127.0.0.1:{self.openai_port}/v1/chat/completions",
            headers={"Authorization": "Bearer rec-test-key-aaaa"},
            body={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": prompt}]},
        )
        self.assertEqual(st, 200)
        _, calls = self._http(
            "GET",
            f"http://127.0.0.1:{self.admin_port}/calls",
            headers={"X-T02-Admin": "adm-test-token-bbbb"},
        )
        self.assertTrue(calls["calls"][0]["contains_ssn"])

    def test_admin_requires_token(self):
        st, _ = self._http("GET", f"http://127.0.0.1:{self.admin_port}/calls")
        self.assertEqual(st, 401)

    def test_fault_http_error(self):
        st, _ = self._http(
            "POST",
            f"http://127.0.0.1:{self.admin_port}/fault",
            headers={"X-T02-Admin": "adm-test-token-bbbb", "Content-Type": "application/json"},
            body={"mode": "http_error", "status": 503},
        )
        self.assertEqual(st, 200)
        cst, _ = self._http(
            "POST",
            f"http://127.0.0.1:{self.openai_port}/v1/chat/completions",
            headers={"Authorization": "Bearer rec-test-key-aaaa"},
            body={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
        )
        self.assertEqual(cst, 503)

    def test_openai_has_no_fault_route(self):
        st, _ = self._http(
            "POST",
            f"http://127.0.0.1:{self.openai_port}/fault",
            headers={"Authorization": "Bearer rec-test-key-aaaa"},
            body={"mode": "http_error"},
        )
        self.assertEqual(st, 404)


if __name__ == "__main__":
    unittest.main()
