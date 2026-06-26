"""E15: internal-infrastructure leakage is surgically masked on the client channel.

ROOT CAUSE (leak-hunt E14): an internal IP/host/URL a real model emits was
*detected* (``detect_ip_leakage``) but never *neutralised* on the client channel,
because (a) ``redact_all`` had no internal-network pattern and (b) the default
``output_ip_leakage_action`` was ``flag`` and ``ip_leakage`` is not in
``_REDACTABLE_OUTPUT_CATEGORIES`` — so a ``flag`` verdict returned the text
unchanged. In a non-block / monitor-mode org with no blocking policy rule, a REAL
internal address therefore egressed raw, asymmetric with PII (always redacted).

FIX (E15):
  * ``patterns.redact_all`` masks internal IPv4 / hostname / internal-URL
    (canonical example/gateway addrs exempt; file paths intentionally left to flag).
  * ``output_guard.sanitize_output_for_verdict`` masks ``ip_leakage`` via the
    deterministic redactor.
  * ``OutputGuard.inspect`` floors ``ip_leakage`` to ``redact`` (was ``flag``),
    while keeping the opt-in hard ``block`` (``output_block_on_ip_leakage``).

These tests pin the masking + the floored action so the leak cannot regress.
"""
from __future__ import annotations

import asyncio

from output_guard import OutputGuard, OutputVerdict, sanitize_output_for_verdict
from patterns import redact_all


# ── redact_all: REAL internal network addresses are masked ──────────────────

def test_redact_all_masks_internal_ipv4():
    out = redact_all("connect to the box at 10.1.2.3 to finish")
    assert "10.1.2.3" not in out
    assert "[INTERNAL_IPV4_REDACTED]" in out


def test_redact_all_masks_192_168_real():
    out = redact_all("the database is at 192.168.50.123 internally")
    assert "192.168.50.123" not in out


def test_redact_all_masks_172_16_real():
    out = redact_all("service runs on 172.16.4.9")
    assert "172.16.4.9" not in out


def test_redact_all_masks_internal_hostname():
    out = redact_all("ssh admin@db-primary.internal for the dump")
    assert "db-primary.internal" not in out


def test_redact_all_masks_internal_url():
    out = redact_all("admin panel is at http://10.0.5.5/admin/login")
    assert "10.0.5.5" not in out


# ── redact_all: false-positive carve-outs are preserved ─────────────────────

def test_redact_all_exempts_example_gateway_addr():
    # the textbook home-router/default-gateway example is NOT infra leakage
    assert "192.168.0.1" in redact_all("your default gateway is usually 192.168.0.1")


def test_redact_all_leaves_public_ip():
    # a public IP (e.g. a DNS resolver) is not internal infra → untouched
    assert "8.8.8.8" in redact_all("set your DNS to 8.8.8.8")


def test_redact_all_leaves_file_path():
    # file paths are far too FP-prone (legit in code answers) → NOT masked here
    assert "/home/ubuntu/app.py" in redact_all("edit /home/ubuntu/app.py and rerun")


# ── sanitize_output_for_verdict: ip_leakage verdict masks the infra ─────────

def test_sanitize_masks_ip_leakage_verdict():
    v = OutputVerdict(action="redact", threat_type="ip_leakage")
    src = "the primary server 10.9.8.7 is currently down"
    out = sanitize_output_for_verdict(src, v, redact_pii_fn=redact_all)
    assert "10.9.8.7" not in out
    assert out != src


def test_sanitize_ip_leakage_no_redactor_is_safe():
    # without a redactor the function must not crash; returns text unchanged
    v = OutputVerdict(action="flag", threat_type="ip_leakage")
    out = sanitize_output_for_verdict("host 10.9.8.7", v, redact_pii_fn=None)
    assert isinstance(out, str)


# ── OutputGuard.inspect: ip_leakage is floored to redact (was flag) ─────────

def _guard_no_tier2() -> OutputGuard:
    # scanner=None disables tier-2 + PII scanner paths; only the deterministic
    # ip_leakage detector runs, so the verdict is not dropped by guard_rated_clean.
    return OutputGuard(None, config={
        "output_pii_enabled": False,
        "output_credential_enabled": False,
        "hallucination_flag_enabled": False,
        "output_tier2_enabled": False,
    })


def test_inspect_floors_ip_leakage_to_redact():
    g = _guard_no_tier2()
    v = asyncio.run(g.inspect("the internal box at 10.9.8.7 hosts the API"))
    assert v.threat_type == "ip_leakage"
    assert v.action == "redact"  # floored from the old 'flag'


def test_inspect_ip_leakage_opt_in_block_preserved():
    g = OutputGuard(None, config={
        "output_pii_enabled": False,
        "output_credential_enabled": False,
        "hallucination_flag_enabled": False,
        "output_tier2_enabled": False,
        "output_block_on_ip_leakage": True,  # org explicitly opted into hard block
    })
    v = asyncio.run(g.inspect("the internal box at 10.9.8.7 hosts the API"))
    assert v.threat_type == "ip_leakage"
    assert v.action == "block"  # opt-in hard block is NOT downgraded


def test_inspect_example_addr_not_flagged():
    g = _guard_no_tier2()
    v = asyncio.run(g.inspect("the default gateway is 192.168.0.1 by convention"))
    # sole match is an example/gateway addr → no ip_leakage verdict at all
    assert v.threat_type != "ip_leakage"
