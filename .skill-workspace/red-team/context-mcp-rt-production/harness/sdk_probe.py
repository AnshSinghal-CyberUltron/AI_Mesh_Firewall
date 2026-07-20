#!/usr/bin/env python3
"""
Documents the exact OpenAI SDK code path that WOULD be used against production,
and captures the live exception/response when no valid GATEWAY_API_KEY exists.

Run with: cd gateway && ./.venv/bin/python \
  ../.skill-workspace/red-team/context-mcp-rt-production/harness/sdk_probe.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import OPENAI_BASE_URL, find_gateway_api_key, save_evidence, Evidence  # noqa: E402

import openai  # noqa: E402


def probe(label: str, api_key: str) -> dict:
    """Exact SDK call pattern the full protocol would use:

        from openai import OpenAI
        client = OpenAI(base_url="https://aimeshgateway.zeroshield.ai/v1", api_key=GATEWAY_API_KEY)
        client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "..."}],
            extra_body={"mcp_context": {"canary": "...", "pii_probe": "..."}},
        )
    """
    request_record = {
        "sdk": "openai-python",
        "sdk_version": openai.__version__,
        "base_url": OPENAI_BASE_URL,
        "api_key_used": "<none>" if not api_key else f"<redacted len={len(api_key)}>",
        "call": (
            "client = OpenAI(base_url='https://aimeshgateway.zeroshield.ai/v1', "
            f"api_key={'GATEWAY_API_KEY (not found)' if not api_key else 'GATEWAY_API_KEY'!r}); "
            "client.chat.completions.create(model='gpt-4o-mini', "
            "messages=[{'role':'user','content':'ping — canary CANARY-sdk-probe'}], "
            "extra_body={'mcp_context': {'canary': 'CANARY-sdk-probe', "
            "'pii_probe': 'SSN 123-45-6789 email redteam@example.com'}})"
        ),
    }
    try:
        client = openai.OpenAI(base_url=OPENAI_BASE_URL, api_key=api_key or None, max_retries=0)
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "ping - canary CANARY-sdk-probe"}],
            extra_body={
                "mcp_context": {
                    "canary": "CANARY-sdk-probe",
                    "pii_probe": "SSN 123-45-6789 email redteam@example.com",
                }
            },
        )
        response_record = {"outcome": "unexpected_success", "response": resp.model_dump()}
    except openai.AuthenticationError as exc:
        # Live 401 returned by the gateway itself (bogus-but-well-formed key).
        response_record = {
            "outcome": "AuthenticationError",
            "status_code": getattr(exc, "status_code", None),
            "message": str(exc),
            "response_body": getattr(exc, "body", None),
        }
    except openai.APIError as exc:
        response_record = {
            "outcome": type(exc).__name__,
            "status_code": getattr(exc, "status_code", None),
            "message": str(exc),
        }
    except openai.OpenAIError as exc:
        # Client-construction failure (e.g. no api_key / no env var at all) --
        # raised locally by the SDK before any network request is made.
        response_record = {
            "outcome": type(exc).__name__,
            "status_code": None,
            "message": str(exc),
            "raised_before_network_call": True,
        }
    ev = Evidence("_gateway_auth_boundary", f"sdk_{label}", request_record, response_record)
    path = save_evidence(ev)
    return {"label": label, "path": str(path), "outcome": response_record.get("outcome")}


def main() -> int:
    key = find_gateway_api_key()
    results = []
    if key:
        results.append(probe("with_real_key", key))
    else:
        results.append(probe("no_key_empty_string", ""))
        results.append(probe("no_key_bogus_token", "sk-redteam-probe-0000000000000000000000000000"))
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
