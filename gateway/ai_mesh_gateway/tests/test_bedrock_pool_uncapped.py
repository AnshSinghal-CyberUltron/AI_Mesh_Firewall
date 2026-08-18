"""Task 11: GATEWAY_BEDROCK_THREAD_POOL_SIZE must not clamp at 256.

Bedrock I/O is network-bound. The detector/entrypoint may set a large pool
(thousands of workers). A programmer max_value=256 serializes in-flight
Tier-2 calls while CPU sits idle.
"""
from __future__ import annotations


def test_bedrock_pool_not_clamped_to_256(monkeypatch):
    from ai_mesh_gateway.scanner import InputScanner

    monkeypatch.setenv("ENABLE_TIER2", "false")
    monkeypatch.setenv("GATEWAY_BEDROCK_THREAD_POOL_SIZE", "4096")
    scanner = InputScanner(thread_pool_size=1)
    try:
        assert scanner._bedrock_executor._max_workers == 4096
    finally:
        scanner._executor.shutdown(wait=False)
        scanner._bedrock_executor.shutdown(wait=False)


def test_bedrock_pool_default_remains_16(monkeypatch):
    from ai_mesh_gateway.scanner import InputScanner

    monkeypatch.setenv("ENABLE_TIER2", "false")
    monkeypatch.delenv("GATEWAY_BEDROCK_THREAD_POOL_SIZE", raising=False)
    scanner = InputScanner(thread_pool_size=1)
    try:
        assert scanner._bedrock_executor._max_workers == 16
    finally:
        scanner._executor.shutdown(wait=False)
        scanner._bedrock_executor.shutdown(wait=False)
