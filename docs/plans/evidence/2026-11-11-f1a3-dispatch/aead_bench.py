"""F1a-3 credential envelope micro-benchmark (prototype, not product code).

Measures, in THREAD CPU time (time.thread_time_ns), the per-operation cost of every step the
F1a-3 design puts on the request path or the apply path:

  aad      canonical length-prefixed AAD encoding of the credential tuple (apply path: once
           per entry; the request path reuses the bytes)
  dec_hot  AES-256-GCM decrypt of one credential with a cached AESGCM(DEK) object (request path)
  dec_new  the same with AESGCM(DEK) constructed per call (no cipher-object cache)
  cold     KEK unwrap of the org DEK + AESGCM(DEK) + credential decrypt (first use per worker)
  b64      base64 decode of nonce + ciphertext (if the view kept the JSON text form)
  header   b"Bearer " + plaintext (the bytes the request carries)
  enc      AES-256-GCM encrypt with a fresh os.urandom(12) nonce (projector side)
  fernet   Fernet decrypt of control's stored token (projector side, once per changed row)
  hmac     the projector's keyed digest of a Fernet token (its memo key, projector side)

It also checks the security properties the design relies on: a round trip decrypts, and a
ciphertext moved to a different (org, base_url, version) fails with InvalidTag.

Every key and credential here is random test material generated in-process; nothing is read
from the environment. Run:  python aead_bench.py [--iters N] [--repeats R]
"""

from __future__ import annotations

import argparse
import base64
import gc
import hashlib
import hmac
import json
import os
import platform
import resource
import struct
import sys
import time

import cryptography
from cryptography.exceptions import InvalidTag
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

AAD_DOMAIN = b"amf.cred.v1"
DEK_DOMAIN = b"amf.dek.v1"


def aad(fields: tuple[str, ...], domain: bytes = AAD_DOMAIN) -> bytes:
    """Domain tag, then each field as a 4-byte big-endian length and its UTF-8 bytes."""
    out = bytearray(struct.pack(">I", len(domain)))
    out += domain
    for field in fields:
        raw = field.encode("utf-8")
        out += struct.pack(">I", len(raw))
        out += raw
    return bytes(out)


def cred_fields(org: str, base_url: str, version: int) -> tuple[str, ...]:
    return (
        "rv2",  # namespace
        org,
        "row-1234567",  # row_id
        "prod-chat",  # alias
        "openai",  # provider
        base_url,
        "gpt-4o-2024-08-06",  # upstream model
        "dek-7f3a9c2e",  # dek id
        str(version),  # credential version
    )


def per_op(fn, iters: int, repeats: int) -> float:
    """Minimum over `repeats` of the mean thread-CPU ns per call over `iters` calls."""
    best = float("inf")
    for _ in range(repeats):
        gc.disable()
        start = time.thread_time_ns()
        for _ in range(iters):
            fn()
        elapsed = time.thread_time_ns() - start
        gc.enable()
        best = min(best, elapsed / iters)
    return best


def rss_kib() -> int:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iters", type=int, default=50_000)
    parser.add_argument("--repeats", type=int, default=7)
    args = parser.parse_args()
    iters, repeats = args.iters, args.repeats

    kek = AESGCM(AESGCM.generate_key(bit_length=256))
    dek_bytes = AESGCM.generate_key(bit_length=256)
    dek_aad = aad(("rv2", "org-acme", "dek-7f3a9c2e", "kek-2026-11"), DEK_DOMAIN)
    dek_nonce = os.urandom(12)
    wrapped = kek.encrypt(dek_nonce, dek_bytes, dek_aad)
    dek = AESGCM(dek_bytes)

    results: dict[str, object] = {
        "python": sys.version.split()[0],
        "cryptography": cryptography.__version__,
        "platform": platform.platform(),
        "iters": iters,
        "repeats": repeats,
        "unit": "thread CPU ns per operation (min over repeats of the mean)",
    }

    fields = cred_fields("org-acme", "https://api.openai.com/v1", 3)
    results["aad_bytes"] = len(aad(fields))
    results["aad"] = per_op(lambda: aad(fields), iters, repeats)

    # Typical provider key lengths: Gemini 39, Mistral 32, OpenAI project keys ~164, and a 256
    # upper case for long custom tokens.
    for length in (39, 164, 256):
        plaintext = os.urandom(length // 2).hex()[:length].encode("ascii")
        cred_aad = aad(fields)
        nonce = os.urandom(12)
        ct = dek.encrypt(nonce, plaintext, cred_aad)
        assert dek.decrypt(nonce, ct, cred_aad) == plaintext
        # Security property: the same ciphertext under a different binding must fail.
        for other in (
            cred_fields("org-evil", "https://api.openai.com/v1", 3),
            cred_fields("org-acme", "https://attacker.example/v1", 3),
            cred_fields("org-acme", "https://api.openai.com/v1", 2),
        ):
            try:
                dek.decrypt(nonce, ct, aad(other))
            except InvalidTag:
                pass
            else:  # pragma: no cover - would be a broken AEAD
                raise SystemExit("ciphertext opened under a different AAD")
        b64n, b64c = base64.b64encode(nonce), base64.b64encode(ct)
        row = f"key{length}"
        results[f"dec_hot_{row}"] = per_op(lambda: dek.decrypt(nonce, ct, cred_aad), iters, repeats)
        results[f"dec_new_{row}"] = per_op(
            lambda: AESGCM(dek_bytes).decrypt(nonce, ct, cred_aad), iters, repeats
        )

        def cold() -> bytes:
            key = kek.decrypt(dek_nonce, wrapped, dek_aad)
            return AESGCM(key).decrypt(nonce, ct, cred_aad)

        results[f"cold_{row}"] = per_op(cold, iters, repeats)
        results[f"b64_{row}"] = per_op(
            lambda: (base64.b64decode(b64n), base64.b64decode(b64c)), iters, repeats
        )
        results[f"header_{row}"] = per_op(lambda: b"Bearer " + plaintext, iters, repeats)
        results[f"enc_{row}"] = per_op(
            lambda: dek.encrypt(os.urandom(12), plaintext, cred_aad), iters, repeats
        )
        fernet = Fernet(Fernet.generate_key())
        token = fernet.encrypt(plaintext)
        results[f"fernet_{row}"] = per_op(lambda: fernet.decrypt(token), iters // 5, repeats)
        memo_key = os.urandom(32)
        results[f"hmac_{row}"] = per_op(
            lambda: hmac.new(memo_key, token, hashlib.sha256).digest()[:16], iters, repeats
        )

    # Memory of a cached cipher object per org (the DEK cache the design bounds with an LRU).
    gc.collect()
    before = rss_kib()
    held = [AESGCM(os.urandom(32)) for _ in range(100_000)]
    gc.collect()
    after = rss_kib()
    results["dek_cache_bytes_per_entry"] = round((after - before) * 1024 / len(held), 1)
    del held

    # Projector round-time arithmetic input: 100k orgs x 5 models full re-encrypt.
    enc164 = results["enc_key164"]
    assert isinstance(enc164, float)
    results["full_reencrypt_500k_s"] = round(enc164 * 500_000 / 1e9, 3)
    print(json.dumps(results, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
