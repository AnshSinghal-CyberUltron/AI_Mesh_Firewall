"""GW17b glue on the MCP request path: transport policy, header attachment, reflection guard.

The credential is acquired by `dispatch.mcp_credentials.CredentialService`, rendered into
`RenderedHeaders` and handed to the broker call; this module decides WHETHER it may be attached
(`attachable`) and whether the broker reply echoed it (`leaks`). A leak withholds the whole reply
(`output_blocked`, DD-14); nothing is partly masked. No function here logs, raises with, or returns
a secret: `leaks` answers with the NAME of the matching form only.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from urllib.parse import quote, urlparse

from gateway_v2.dispatch.mcp_broker import registered_hosts
from gateway_v2.dispatch.mcp_credentials import CredentialService, render
from gateway_v2.dispatch.mcp_transports import TRANSPORTS
from gateway_v2.domain import posture
from gateway_v2.domain.mcp_credential import CredentialRefusal, RenderedHeaders, UpstreamCredential
from gateway_v2.edge.mcp_registry import ServerEntry
from gateway_v2.runtime.resources_mcp import McpCredentialBounds

__all__ = ("Attachment", "attachable", "leaks", "prepare")

_LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})
_SECURE_SCHEMES = frozenset({"https", "wss"})


def attachable(
    *, transport: str, url: str, plaintext_hosts: frozenset[str], allowed_hosts: tuple[str, ...]
) -> str | None:
    """``None`` if a credential may be attached, else the refusal code.

    Remote transports only (stdio never carries one, R12.6); `allowed_hosts` must be exactly the
    registered host (R12.4); plaintext (`http`/`ws`) to a non-loopback host needs an exact-match
    allow entry, default none.
    """
    spec = TRANSPORTS.get(transport)
    if spec is None or not spec.remote:
        return posture.MCP_UPSTREAM_CREDENTIAL_INVALID
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if parsed.scheme not in spec.schemes or not host:
        return posture.MCP_UPSTREAM_CREDENTIAL_INVALID
    if allowed_hosts != (host,):
        return "mcp_destination_refused"
    plaintext = parsed.scheme not in _SECURE_SCHEMES
    if plaintext and host not in _LOOPBACK and host not in plaintext_hosts:
        return posture.MCP_UPSTREAM_INSECURE_TRANSPORT
    return None


def _secrets_of(cred: UpstreamCredential) -> list[tuple[str, str]]:
    out = [("plain", cred.token.strip())]
    if cred.kind == "basic":
        out.append(("plain", cred.username))
        pair = f"{cred.username}:{cred.token.strip()}".encode()
        out.append(("b64", base64.b64encode(pair).decode()))
    return [(form, value) for form, value in out if value]


def _forms(secret: str, min_len: int) -> list[tuple[str, str]]:
    raw = secret.encode()
    forms = [
        ("plain", secret), ("case", secret.lower()), ("case", secret.upper()),
        ("hex", raw.hex()), ("pct", quote(secret, safe="")),
        ("json", json.dumps(secret)[1:-1]),
    ]
    skips = (0, 2, 3)  # leading chars a 0/1/2-byte shift perturbs
    for pad, skip in enumerate(skips):
        shifted = b"\x00" * pad + raw
        for encoder in (base64.b64encode, base64.urlsafe_b64encode):
            text = encoder(shifted).decode().rstrip("=")
            if pad == 0:
                forms.append(("b64", text))
                forms.append(("b64", encoder(shifted).decode()))
            elif len(text) - skip - 2 >= min_len - 2:
                forms.append(("b64", text[skip : len(text) - 2]))
    forms.append(("prefix", secret[:min_len]))
    return [(form, value) for form, value in forms if len(value) >= min_len - 2]


def _leaves(node: object, depth: int = 0) -> Iterator[str]:
    """Every string leaf and every object key; a leaf that parses as JSON is decoded once more."""
    if isinstance(node, str):
        yield node
        if depth == 0 and node[:1] in "{[":
            try:
                yield from _leaves(json.loads(node), 1)
            except ValueError:
                return
    elif isinstance(node, dict):
        for key, value in node.items():
            yield str(key)
            yield from _leaves(value, depth)
    elif isinstance(node, list):
        for item in node:
            yield from _leaves(item, depth)


def leaks(cred: UpstreamCredential, envelope: object, *, min_len: int) -> str | None:
    """The NAME of the form of a credential found in the reply (plain, case, hex, b64, pct, json,
    prefix), or ``None``. Scans every string leaf, every key, and the raw serialised bytes."""
    try:
        raw = json.dumps(envelope, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return "plain"  # unserialisable reply cannot be proven clean: withhold
    haystacks = [raw, *_leaves(envelope)]
    for _, secret in _secrets_of(cred):
        for form, needle in _forms(secret, min_len):
            if any(needle in hay for hay in haystacks):
                return form
    return None


@dataclass(frozen=True, slots=True)
class Attachment:
    """The rendered headers for one call and the credential they came from (for the leak check)."""

    headers: RenderedHeaders = field(repr=False)
    credential: UpstreamCredential = field(repr=False)

    def __repr__(self) -> str:
        return "<redacted>"

    __str__ = __repr__


async def prepare(
    service: CredentialService | None,
    bounds: McpCredentialBounds | None,
    plaintext_hosts: frozenset[str],
    org: str,
    server: ServerEntry,
) -> Attachment | str | None:
    """``None`` = ``auth none`` (nothing attached); an :class:`Attachment`; or a refusal code.

    Order: transport policy first (no fetch for a destination that could never receive it), then
    the credential, then rendering. Every failure is a declared code and makes zero broker calls.
    """
    if server.auth_type == "none":
        return None
    if service is None or bounds is None:
        return posture.MCP_UPSTREAM_CREDENTIAL_UNAVAILABLE
    refused = attachable(
        transport=server.transport,
        url=server.url,
        plaintext_hosts=plaintext_hosts,
        allowed_hosts=tuple(registered_hosts(server.url)),
    )
    if refused is not None:
        return refused
    cred = await service.acquire(org, server.slug, server.auth_type)
    if isinstance(cred, CredentialRefusal):
        return cred.code
    rendered = render(cred, bounds)
    if isinstance(rendered, CredentialRefusal):
        return rendered.code
    return Attachment(rendered, cred)
