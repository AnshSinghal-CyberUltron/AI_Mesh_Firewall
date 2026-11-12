"""F1a-2 evidence for parent §6.4: decrypt per request instead of holding plaintext.

Run with the control venv (the gateway venv has no `cryptography`):

    nice -n 10 /home/claude/work/venv-ctl312/bin/python -I aead_bench.py

AES-256-GCM over a 51-byte API key with a ~120-byte length-prefixed AAD (the §7.1 tuple shape),
thread CPU per operation. Two variants: an `AESGCM` object cached per kid (the design), and one
built per request (what to avoid). Also Fernet decrypt (what the projector does per changed row).
"""

from __future__ import annotations

import os
import struct
import sys
import time

import cryptography
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def aad(*parts: str) -> bytes:
    out = bytearray()
    for part in parts:
        raw = part.encode("utf-8")
        out += struct.pack(">I", len(raw)) + raw
    return bytes(out)


def per_op(fn, n: int) -> float:
    t0 = time.thread_time()
    for _ in range(n):
        fn()
    return (time.thread_time() - t0) / n * 1e6


def main() -> int:
    print(f"python={sys.version.split()[0]} cryptography={cryptography.__version__} (thread CPU)")
    key = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    secret = os.urandom(51)
    bound = aad("{rv2}:model", "org-000123", "4242", "prod-chat", "azure",
                "https://acme-prod.openai.azure.com", "k2026q4")
    cached = AESGCM(key)
    ct = cached.encrypt(nonce, secret, bound)
    assert cached.decrypt(nonce, ct, bound) == secret
    n = 200_000
    print(f"AES-256-GCM decrypt, cached AESGCM per kid : {per_op(lambda: cached.decrypt(nonce, ct, bound), n):.2f} us")
    print(f"AES-256-GCM decrypt, AESGCM built per call: {per_op(lambda: AESGCM(key).decrypt(nonce, ct, bound), n):.2f} us")
    print(f"AES-256-GCM encrypt (projector, per changed row): {per_op(lambda: cached.encrypt(os.urandom(12), secret, bound), n):.2f} us")
    f = Fernet(Fernet.generate_key())
    token = f.encrypt(secret)
    print(f"Fernet decrypt (projector, per changed row): {per_op(lambda: f.decrypt(token), 50_000):.2f} us")
    print(f"Fernet token length for a 51-byte key: {len(token)} chars")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
