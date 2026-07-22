"""STRICT OPERATOR CONTROL — round-2 adversarial findings (2026-07-23).

A second adversarial sweep (threshold governance, encoded evasion, rewrite fidelity,
streaming regression-hunt, live config propagation — 503 cells, 500 matched) found 3
NEW raw-egress / action-fidelity leaks under an explicit redact/rewrite. These lock
them:

1. streaming redact under token-by-token chunking released the buffer one CHAR at a
   time and re-redacted each fragment in isolation => whole tokens egressed raw.
2. a \\u / \\x backslash-escaped secret was DETECTED (redact verdict) but not masked
   by the sanitizer => shipped in the 200 body, client-recoverable via unicode_escape.
3. the rewrite residual net used unscoped redact_all, which leaves internal file paths
   raw (FP-safe design) => an ip_leakage=rewrite that echoed /home/svc/.ssh/id_rsa
   shipped it verbatim.
"""

from __future__ import annotations

import codecs
import json
import unittest

from ai_mesh_gateway.output_guard import OutputGuard
from ai_mesh_gateway.scanner import InputScanner
from ai_mesh_gateway.secure_streaming import SecureStreamingResponse
from patterns import redact_all, redact_all_scoped


def _sse(t: str) -> str:
    return "data: " + json.dumps({"choices": [{"delta": {"content": t}}]}) + "\n\n"


_REDACT_ALL_CFG = {
    "output_guard_enabled": True, "output_tier2_enabled": False,
    "output_pii_enabled": True, "output_pii_action": "redact",
    "output_credential_enabled": True, "output_credential_action": "redact",
    "output_ip_leakage_enabled": True, "output_ip_leakage_action": "redact",
    "output_policy_enabled": False, "output_policy_action": "allow",
    "factuality_check_enabled": False, "output_hallucination_action": "allow",
}


async def _stream(text, chunk, *, buf_bytes=4096, buf_chunks=64):
    sc = InputScanner()

    async def inner():
        for i in range(0, len(text), chunk):
            yield _sse(text[i:i + chunk])
        yield "data: [DONE]\n\n"
    sec = SecureStreamingResponse(inner_generator=inner(), scanner=sc,
                                  output_guard=OutputGuard(sc, _REDACT_ALL_CFG),
                                  buffer_max_bytes=buf_bytes, max_buffer_chunks=buf_chunks,
                                  enforcement_mode="block")
    out = []
    async for ch in sec:
        if ch.startswith("data: ") and "[DONE]" not in ch and "output_blocked" not in ch:
            try:
                d = json.loads(ch[6:].strip())
            except ValueError:
                continue
            c = (d.get("choices") or [{}])[0].get("delta", {}).get("content")
            if c:
                out.append(c)
    return "".join(out)


class Round2Finding3_StreamingTokenByToken(unittest.IsolatedAsyncioTestCase):
    """Token-by-token (1-N char) streaming under redact must not release raw tokens
    a char at a time. A long multi-secret response must mask every secret at every
    small chunk size and buffer config."""

    SECRETS = ["john.doe@acme.com", "123-45-6789",
               "sk-proj-AbCdEf0123456789AbCdEf0123456789", "10.0.0.5", "AKIAIOSFODNN7EXAMPLE"]
    FILLER = " the quarterly report is ready and the numbers look consistent with guidance."
    TEXT = ("Contact john.doe@acme.com SSN 123-45-6789." + FILLER * 8
            + " Key sk-proj-AbCdEf0123456789AbCdEf0123456789 here." + FILLER * 8
            + " Host 10.0.0.5 and aws AKIAIOSFODNN7EXAMPLE done.")

    async def test_no_raw_secret_leaks_token_by_token(self):
        for buf_bytes, buf_chunks in ((4096, 64), (1_000_000, 1000), (256, 16)):
            for chunk in (1, 2, 3, 5, 8, 24):
                out = await _stream(self.TEXT, chunk, buf_bytes=buf_bytes, buf_chunks=buf_chunks)
                for s in self.SECRETS:
                    with self.subTest(buf=(buf_bytes, buf_chunks), chunk=chunk, secret=s[:10]):
                        self.assertNotIn(s, out, f"raw {s[:12]} leaked at chunk={chunk}")

    async def test_clean_stream_intact_token_by_token(self):
        clean = "The report is ready." + self.FILLER * 20
        for chunk in (1, 7, 50):
            out = await _stream(clean, chunk)
            with self.subTest(chunk=chunk):
                self.assertEqual(out, clean)


class Round2Finding1_BackslashEscapeRedaction(unittest.TestCase):
    """A \\u / \\x-escaped secret in a mixed maskable carrier must be masked under
    redact (not shipped raw and client-recoverable). Benign single escapes are left."""

    def _esc_u(self, s):
        return "".join(f"\\u{ord(c):04x}" for c in s)

    def _esc_x(self, s):
        return "".join(f"\\x{ord(c):02x}" for c in s)

    def test_u_escaped_email_masked_under_pii_redact(self):
        esc = self._esc_u("john.doe@acme.com")
        text = f"contact {esc} for access, also SSN 123-45-6789."
        out = redact_all_scoped(text, {"pii"})
        self.assertNotIn(esc, out)
        # not recoverable after decode
        self.assertNotIn("john.doe@acme.com", codecs.decode(
            "".join(c for c in out if True), "unicode_escape", "ignore"))

    def test_x_escaped_key_masked_under_credential_redact(self):
        esc = self._esc_x("AKIAIOSFODNN7EXAMPLE")
        text = f"key1 aws_access_key_id={esc} and a note."
        out = redact_all_scoped(text, {"credential"})
        self.assertNotIn(esc, out)

    def test_benign_single_escape_untouched(self):
        benign = r'The regex AB matches AB and path C:\x41 here. 50% off.'
        self.assertEqual(redact_all_scoped(benign, {"pii"}), benign)
        self.assertEqual(redact_all(benign), benign)


class Round2Finding2_RewriteResidualFilePath(unittest.TestCase):
    """The rewrite residual net must cover internal file paths for the ip_leakage
    class (all classes + file paths), while mask-all keeps its FP-safe behaviour for
    other classes."""

    ECHOED = "Please avoid internal paths like /home/svc/.ssh/id_rsa or host 10.1.2.3 and key AKIAIOSFODNN7EXAMPLE."

    def test_ip_leakage_rewrite_residual_masks_file_path(self):
        res = redact_all_scoped(self.ECHOED, {"pii", "credential", "ip_leakage"})
        self.assertNotIn("/home/svc/.ssh/id_rsa", res)
        self.assertNotIn("10.1.2.3", res)
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", res)

    def test_mask_all_residual_leaves_file_path_fp_safe(self):
        # non-ip_leakage rewrite residual keeps mask-all's historical FP-avoidance
        self.assertIn("/home/svc/.ssh/id_rsa", redact_all(self.ECHOED))


if __name__ == "__main__":
    unittest.main()
