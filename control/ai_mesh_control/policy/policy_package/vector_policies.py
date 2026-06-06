"""
VECTOR domain policy catalog for RAG Data Protection.

This domain uses the ``VectorCollectionPolicy`` shape (see
``policy.vector_models.VectorCollectionPolicy``) rather than the generic
``Policy`` + ``Rule`` pair used by other domains. Each entry below is a
representative access-control profile for a vector DB collection, covering
namespace isolation, collection-level RBAC, content filtering, PII scanning,
indirect-prompt-injection (context) scanning, and embedding anomaly detection.

Pure data only — no Django imports. The dicts here mirror the model field
names exactly so they can be fed straight into ``VectorCollectionPolicy``
``bulk_create`` / fixtures / Redis payloads.

Field reference (from VectorCollectionPolicy):
  - vector_db_type   ∈ {"pinecone", "milvus", "custom"}
  - default_action   ∈ {"allow", "deny", "monitor"}
  - allowed_operations ⊆ {"query", "insert", "update", "delete"}
  - anomaly_distance_threshold ∈ [0.0, 1.0]
  - severity         ∈ {"CRITICAL", "HIGH", "MEDIUM", "LOW"}  (informational)
"""

from __future__ import annotations

from typing import Any

# --- Domain enums (kept in sync with policy.vector_models) -------------------
VALID_VECTOR_DB_TYPES = ("pinecone", "milvus", "custom")
VALID_VECTOR_ACTIONS = ("allow", "deny", "monitor")
VALID_VECTOR_OPERATIONS = ("query", "insert", "update", "delete")
VALID_SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW")


# Module-level catalog. Each dict is a self-contained VectorCollectionPolicy
# profile across representative collections + control postures.
VECTOR_POLICIES: list[dict[str, Any]] = [
    {
        "key": "VEC_DOCS_STRICT",
        "name": "Docs collection — strict isolation",
        "collection_name": "docs",
        "vector_db_type": "pinecone",
        "namespace": "",
        "default_action": "deny",
        "allowed_operations": ["query"],
        "max_results_per_query": 10,
        "max_query_length": 2000,
        "sensitive_fields": ["ssn", "email", "phone"],
        "require_context_scan": True,
        "block_sensitive_documents": True,
        "anomaly_distance_threshold": 0.85,
        "severity": "HIGH",
        "description": "Read-only docs retrieval that scans for PII and indirect prompt injection before returning.",
        "frameworks": ["OWASP-LLM08", "OWASP-LLM06"],
    },
    {
        "key": "VEC_KNOWLEDGE_BASE_READONLY",
        "name": "Knowledge base — read-only",
        "collection_name": "knowledge-base",
        "vector_db_type": "pinecone",
        "namespace": "public",
        "default_action": "allow",
        "allowed_operations": ["query"],
        "max_results_per_query": 20,
        "max_query_length": 4000,
        "sensitive_fields": ["email"],
        "require_context_scan": True,
        "block_sensitive_documents": False,
        "anomaly_distance_threshold": 0.90,
        "severity": "MEDIUM",
        "description": "Public knowledge-base allowing only query operations with relaxed anomaly tolerance.",
        "frameworks": ["OWASP-LLM08"],
    },
    {
        "key": "VEC_DEFAULT_MONITOR",
        "name": "Default collection — monitor only",
        "collection_name": "default",
        "vector_db_type": "pinecone",
        "namespace": "",
        "default_action": "monitor",
        "allowed_operations": ["query", "insert", "update", "delete"],
        "max_results_per_query": 25,
        "max_query_length": 4000,
        "sensitive_fields": [],
        "require_context_scan": True,
        "block_sensitive_documents": False,
        "anomaly_distance_threshold": 0.92,
        "severity": "LOW",
        "description": "Catch-all observe-only profile that logs every operation without blocking traffic.",
        "frameworks": ["OWASP-LLM08"],
    },
    {
        "key": "VEC_CUSTOMER_KB_NO_WRITE",
        "name": "Customer data — deny writes, scan PII",
        "collection_name": "customer-kb",
        "vector_db_type": "pinecone",
        "namespace": "customers",
        "default_action": "deny",
        "allowed_operations": ["query"],
        "max_results_per_query": 15,
        "max_query_length": 3000,
        "sensitive_fields": ["ssn", "email", "phone", "address", "credit_card"],
        "require_context_scan": True,
        "block_sensitive_documents": True,
        "anomaly_distance_threshold": 0.80,
        "severity": "HIGH",
        "description": "Customer collection that permits reads only and blocks any document carrying PII.",
        "frameworks": ["OWASP-LLM06", "GDPR", "CCPA"],
    },
    {
        "key": "VEC_LEGAL_CASES_STRICT",
        "name": "Legal cases — strict, low anomaly threshold",
        "collection_name": "legal-cases",
        "vector_db_type": "pinecone",
        "namespace": "litigation",
        "default_action": "deny",
        "allowed_operations": ["query"],
        "max_results_per_query": 8,
        "max_query_length": 2500,
        "sensitive_fields": ["ssn", "email", "phone", "case_number", "client_name"],
        "require_context_scan": True,
        "block_sensitive_documents": True,
        "anomaly_distance_threshold": 0.65,
        "severity": "CRITICAL",
        "description": "Privileged legal corpus with a tight anomaly threshold and PII blocking on every retrieval.",
        "frameworks": ["OWASP-LLM08", "GDPR"],
    },
    {
        "key": "VEC_CODE_EMBEDDINGS",
        "name": "Code embeddings — secret-aware retrieval",
        "collection_name": "code-embeddings",
        "vector_db_type": "pinecone",
        "namespace": "repos",
        "default_action": "deny",
        "allowed_operations": ["query", "insert"],
        "max_results_per_query": 12,
        "max_query_length": 6000,
        "sensitive_fields": ["api_key", "token", "secret", "password"],
        "require_context_scan": True,
        "block_sensitive_documents": True,
        "anomaly_distance_threshold": 0.78,
        "severity": "HIGH",
        "description": "Source-code embedding store that blocks chunks containing credentials or secrets.",
        "frameworks": ["OWASP-LLM06", "OWASP-LLM08"],
    },
    {
        "key": "VEC_INGESTION_INSERT_ALLOWED",
        "name": "Ingestion pipeline — insert allowed",
        "collection_name": "ingestion-staging",
        "vector_db_type": "pinecone",
        "namespace": "staging",
        "default_action": "allow",
        "allowed_operations": ["query", "insert", "update"],
        "max_results_per_query": 30,
        "max_query_length": 8000,
        "sensitive_fields": ["email", "phone"],
        "require_context_scan": True,
        "block_sensitive_documents": True,
        "anomaly_distance_threshold": 0.88,
        "severity": "MEDIUM",
        "description": "Write-capable staging collection used by ingestion jobs, scanning new documents for PII.",
        "frameworks": ["OWASP-LLM06"],
    },
    {
        "key": "VEC_MILVUS_RESEARCH",
        "name": "Research corpus (Milvus) — partitioned reads",
        "collection_name": "research-corpus",
        "vector_db_type": "milvus",
        "namespace": "papers",
        "default_action": "deny",
        "allowed_operations": ["query"],
        "max_results_per_query": 50,
        "max_query_length": 5000,
        "sensitive_fields": ["author_email"],
        "require_context_scan": True,
        "block_sensitive_documents": False,
        "anomaly_distance_threshold": 0.87,
        "severity": "MEDIUM",
        "description": "Milvus-backed research collection demonstrating partition-scoped read-only access.",
        "frameworks": ["OWASP-LLM08"],
    },
    {
        "key": "VEC_CUSTOM_DB_INTERNAL",
        "name": "Internal wiki (custom DB) — monitored reads",
        "collection_name": "internal-wiki",
        "vector_db_type": "custom",
        "namespace": "eng",
        "default_action": "monitor",
        "allowed_operations": ["query", "insert"],
        "max_results_per_query": 18,
        "max_query_length": 4000,
        "sensitive_fields": ["employee_id", "email"],
        "require_context_scan": True,
        "block_sensitive_documents": False,
        "anomaly_distance_threshold": 0.86,
        "severity": "MEDIUM",
        "description": "Custom vector backend example for an internal wiki with observe-only enforcement.",
        "frameworks": ["OWASP-LLM08"],
    },
    {
        "key": "VEC_RESTRICTED_LOCKDOWN",
        "name": "Restricted collection — query-only lockdown",
        "collection_name": "restricted",
        "vector_db_type": "pinecone",
        "namespace": "classified",
        "default_action": "deny",
        "allowed_operations": ["query"],
        "max_results_per_query": 5,
        "max_query_length": 1500,
        "sensitive_fields": ["ssn", "email", "phone", "clearance", "secret"],
        "require_context_scan": True,
        "block_sensitive_documents": True,
        "anomaly_distance_threshold": 0.60,
        "severity": "CRITICAL",
        "description": "Highest-sensitivity collection denying everything but query and blocking any sensitive document.",
        "frameworks": ["OWASP-LLM06", "OWASP-LLM08"],
    },
    {
        "key": "VEC_CONTEXT_SCAN_FOCUSED",
        "name": "Untrusted web cache — context-scan focused",
        "collection_name": "web-cache",
        "vector_db_type": "pinecone",
        "namespace": "crawl",
        "default_action": "deny",
        "allowed_operations": ["query"],
        "max_results_per_query": 10,
        "max_query_length": 3000,
        "sensitive_fields": [],
        "require_context_scan": True,
        "block_sensitive_documents": True,
        "anomaly_distance_threshold": 0.90,
        "severity": "HIGH",
        "description": "Crawled-content collection focused on indirect prompt injection scanning of retrieved context.",
        "frameworks": ["OWASP-LLM01", "OWASP-LLM08"],
    },
    {
        "key": "VEC_ANOMALY_DETECTION_TIGHT",
        "name": "High-value KB — anomaly-detection tuned",
        "collection_name": "high-value-kb",
        "vector_db_type": "milvus",
        "namespace": "finance",
        "default_action": "deny",
        "allowed_operations": ["query"],
        "max_results_per_query": 10,
        "max_query_length": 2000,
        "sensitive_fields": ["account_number", "email"],
        "require_context_scan": True,
        "block_sensitive_documents": True,
        "anomaly_distance_threshold": 0.55,
        "severity": "HIGH",
        "description": "Finance knowledge base tuned for tight embedding anomaly detection to catch poisoning probes.",
        "frameworks": ["OWASP-LLM03", "OWASP-LLM08"],
    },
    {
        "key": "VEC_SUPPORT_TICKETS_MONITOR",
        "name": "Support tickets — monitored PII scan",
        "collection_name": "support-tickets",
        "vector_db_type": "custom",
        "namespace": "tickets",
        "default_action": "monitor",
        "allowed_operations": ["query", "insert"],
        "max_results_per_query": 20,
        "max_query_length": 3500,
        "sensitive_fields": ["email", "phone", "order_id"],
        "require_context_scan": True,
        "block_sensitive_documents": False,
        "anomaly_distance_threshold": 0.83,
        "severity": "MEDIUM",
        "description": "Support ticket store that observes traffic while still scanning retrieved documents for PII.",
        "frameworks": ["OWASP-LLM06", "GDPR"],
    },
]


def _validate_catalog() -> None:
    """Assert structural integrity, field types, and value ranges."""
    required_keys = {
        "key",
        "name",
        "collection_name",
        "vector_db_type",
        "namespace",
        "default_action",
        "allowed_operations",
        "max_results_per_query",
        "max_query_length",
        "sensitive_fields",
        "require_context_scan",
        "block_sensitive_documents",
        "anomaly_distance_threshold",
        "severity",
        "description",
        "frameworks",
    }

    seen_keys: set[str] = set()
    for entry in VECTOR_POLICIES:
        # presence of all required fields
        missing = required_keys - set(entry.keys())
        assert not missing, f"{entry.get('key')!r} missing fields: {sorted(missing)}"

        key = entry["key"]
        assert isinstance(key, str) and key, "key must be a non-empty string"
        assert key.isupper(), f"key must be UPPER_SNAKE: {key!r}"
        assert " " not in key, f"key must not contain spaces: {key!r}"
        assert key not in seen_keys, f"duplicate key: {key!r}"
        seen_keys.add(key)

        assert isinstance(entry["name"], str) and entry["name"], f"{key}: name must be non-empty str"
        assert isinstance(entry["collection_name"], str) and entry["collection_name"], (
            f"{key}: collection_name must be non-empty str"
        )
        assert isinstance(entry["namespace"], str), f"{key}: namespace must be str"

        assert entry["vector_db_type"] in VALID_VECTOR_DB_TYPES, (
            f"{key}: invalid vector_db_type {entry['vector_db_type']!r}"
        )
        assert entry["default_action"] in VALID_VECTOR_ACTIONS, (
            f"{key}: invalid default_action {entry['default_action']!r}"
        )
        assert entry["severity"] in VALID_SEVERITIES, (
            f"{key}: invalid severity {entry['severity']!r}"
        )

        ops = entry["allowed_operations"]
        assert isinstance(ops, list) and ops, f"{key}: allowed_operations must be a non-empty list"
        assert all(op in VALID_VECTOR_OPERATIONS for op in ops), (
            f"{key}: allowed_operations has invalid op: {ops}"
        )
        assert len(ops) == len(set(ops)), f"{key}: allowed_operations has duplicates: {ops}"

        for int_field in ("max_results_per_query", "max_query_length"):
            val = entry[int_field]
            assert isinstance(val, int) and not isinstance(val, bool), (
                f"{key}: {int_field} must be int"
            )
            assert val > 0, f"{key}: {int_field} must be positive, got {val}"

        sf = entry["sensitive_fields"]
        assert isinstance(sf, list), f"{key}: sensitive_fields must be a list"
        assert all(isinstance(f, str) for f in sf), f"{key}: sensitive_fields must be strings"

        assert isinstance(entry["require_context_scan"], bool), (
            f"{key}: require_context_scan must be bool"
        )
        assert isinstance(entry["block_sensitive_documents"], bool), (
            f"{key}: block_sensitive_documents must be bool"
        )

        thr = entry["anomaly_distance_threshold"]
        assert isinstance(thr, float), f"{key}: anomaly_distance_threshold must be float"
        assert 0.0 <= thr <= 1.0, (
            f"{key}: anomaly_distance_threshold out of range [0.0, 1.0]: {thr}"
        )

        assert isinstance(entry["description"], str) and entry["description"], (
            f"{key}: description must be non-empty str"
        )
        fw = entry["frameworks"]
        assert isinstance(fw, list) and fw, f"{key}: frameworks must be a non-empty list"
        assert all(isinstance(f, str) and f for f in fw), (
            f"{key}: frameworks must be non-empty strings"
        )

    assert len(VECTOR_POLICIES) >= 12, (
        f"catalog must define at least 12 policies, got {len(VECTOR_POLICIES)}"
    )


def catalog_metadata() -> dict[str, Any]:
    """Summary metadata for the vector policy catalog."""
    db_types = sorted({p["vector_db_type"] for p in VECTOR_POLICIES})
    actions = sorted({p["default_action"] for p in VECTOR_POLICIES})
    return {
        "domain": "vector",
        "shape": "VectorCollectionPolicy",
        "policy_count": len(VECTOR_POLICIES),
        "vector_db_types": db_types,
        "default_actions": actions,
        "source": "policy.policy_package.vector_policies",
    }


# Validate at import time so a malformed catalog fails fast.
_validate_catalog()


if __name__ == "__main__":
    _validate_catalog()
    meta = catalog_metadata()
    print(f"VECTOR_POLICIES: {len(VECTOR_POLICIES)} policies validated OK")
    print(f"  db types : {meta['vector_db_types']}")
    print(f"  actions  : {meta['default_actions']}")
    for p in VECTOR_POLICIES:
        print(
            f"  - {p['key']:<32} {p['vector_db_type']:<9} "
            f"{p['default_action']:<8} thr={p['anomaly_distance_threshold']:<5} "
            f"[{p['severity']}]"
        )
