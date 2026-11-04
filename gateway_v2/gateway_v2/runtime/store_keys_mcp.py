"""GW32 / GW17b / GW17c key layout, kept together and merge-friendly beside `store_keys.py`.

Shares the `{rv2}` namespace hash tag with `StoreKeys`, so one WATCH/MULTI covers every key of a
family. Builders reject an empty identifier (the `budget_*` idiom): an empty org or key id would
otherwise collapse every tenant onto one key.
"""

from __future__ import annotations

from dataclasses import dataclass

from gateway_v2.runtime.store_keys import DEFAULT_NAMESPACE


def _need(name: str, value: str) -> str:
    if not value:
        raise ValueError(f"{name} must not be empty")
    return value


@dataclass(frozen=True, slots=True)
class McpStoreKeys:
    """Key names for the MCP cards under one namespace."""

    namespace: str = DEFAULT_NAMESPACE

    def session(self, session_hash: str) -> str:
        return f"{self.namespace}:mcp:sess:{_need('session_hash', session_hash)}"

    def sessions_by_key(self, key_id: str) -> str:
        return f"{self.namespace}:mcp:sesskey:{_need('key_id', key_id)}"

    def sessions_by_org(self, org: str) -> str:
        return f"{self.namespace}:mcp:sessorg:{_need('org', org)}"

    def sessions_global(self) -> str:
        return f"{self.namespace}:mcp:sessall"

    def cap_used(self, key_id: str) -> str:
        return f"{self.namespace}:mcp:cap:{_need('key_id', key_id)}"

    def refresh_lock(self, org: str, slug: str) -> str:
        return f"{self.namespace}:mcp:refresh:{_need('org', org)}:{_need('slug', slug)}"

    def oauth_client(self, client_id: str) -> str:
        return f"{self.namespace}:oauth:client:{_need('client_id', client_id)}"

    def oauth_clients_index(self) -> str:
        return f"{self.namespace}:oauth:clients"

    def oauth_clients_by_source(self, source_hash: str) -> str:
        return f"{self.namespace}:oauth:src:{_need('source_hash', source_hash)}"

    def oauth_code(self, code_hash: str) -> str:
        return f"{self.namespace}:oauth:code:{_need('code_hash', code_hash)}"

    def oauth_access(self, token_hash: str) -> str:
        return f"{self.namespace}:oauth:at:{_need('token_hash', token_hash)}"

    def oauth_refresh(self, token_hash: str) -> str:
        return f"{self.namespace}:oauth:rt:{_need('token_hash', token_hash)}"

    def oauth_family(self, family_id: str) -> str:
        return f"{self.namespace}:oauth:fam:{_need('family_id', family_id)}"

    def oauth_form(self, form_hash: str) -> str:
        return f"{self.namespace}:oauth:form:{_need('form_hash', form_hash)}"

    def oauth_failures(self, source_hash: str) -> str:
        return f"{self.namespace}:oauth:fail:{_need('source_hash', source_hash)}"
