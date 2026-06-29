"""
Aggregated organization policy package catalog.

Combines the per-domain catalogs (pipeline / rag / mcp / vector) into one
package of 50+ policies, each a category with multiple underlying rules.

- pipeline / rag / mcp policies → ``Policy`` + ``Rule`` rows (policy.models).
- vector policies → ``VectorCollectionPolicy`` rows (policy.vector_models).

Seeded per-organization by ``policy.policy_package.seed`` and the
``seed_policy_package`` management command. Pure data + validation; no Django
imports here so it can be imported and validated standalone.
"""

from __future__ import annotations

import re
from typing import Any

from .pipeline_policies import POLICIES as _PIPELINE
from .rag_policies import POLICIES as _RAG
from .mcp_policies import POLICIES as _MCP
from .vector_policies import VECTOR_POLICIES as _VECTOR

PACKAGE_ID = "org_policy_package_v1"
PACKAGE_VERSION = "1.0.0"
MIN_POLICY_COUNT = 50

_VALID_DOMAINS = {"pipeline", "rag", "mcp"}
_VALID_SEVERITY = {"CRITICAL", "HIGH", "MEDIUM", "LOW"}
_VALID_ACTIONS = {"block", "redact", "monitor", "rewrite", "model_downgrade"}
_VALID_RULE_TYPES = {"regex", "keywords"}
_VALID_FIELDS = {"prompt", "response", "both"}
_VALID_STAGES = {"", "query", "retriever", "ranker", "generator"}

_VALID_VECTOR_DB = {"pinecone", "milvus", "custom"}
_VALID_VECTOR_ACTION = {"allow", "deny", "monitor"}
_VALID_VECTOR_OPS = {"query", "insert", "update", "delete"}

# Ordered list of the Policy+Rule domain specs.
_POLICY_SPECS: list[dict[str, Any]] = [*_PIPELINE, *_RAG, *_MCP]
_VECTOR_SPECS: list[dict[str, Any]] = list(_VECTOR)


def _validate() -> None:
    seen_keys: set[str] = set()
    if len(_POLICY_SPECS) + len(_VECTOR_SPECS) < MIN_POLICY_COUNT:
        raise ValueError(
            f"policy package must define >= {MIN_POLICY_COUNT} policies, "
            f"got {len(_POLICY_SPECS)} policy + {len(_VECTOR_SPECS)} vector"
        )
    for spec in _POLICY_SPECS:
        key = spec["key"]
        if key in seen_keys:
            raise ValueError(f"duplicate policy key: {key}")
        seen_keys.add(key)
        if spec["domain"] not in _VALID_DOMAINS:
            raise ValueError(f"{key}: invalid domain {spec['domain']!r}")
        if spec["severity"] not in _VALID_SEVERITY:
            raise ValueError(f"{key}: invalid severity {spec['severity']!r}")
        rules = spec.get("rules") or []
        if len(rules) < 2:
            raise ValueError(f"{key}: a policy must have >= 2 rules, got {len(rules)}")
        for r in rules:
            if r["rule_type"] not in _VALID_RULE_TYPES:
                raise ValueError(f"{key}: invalid rule_type {r['rule_type']!r}")
            if r["action"] not in _VALID_ACTIONS:
                raise ValueError(f"{key}: invalid action {r['action']!r}")
            if r.get("field", "both") not in _VALID_FIELDS:
                raise ValueError(f"{key}: invalid field {r.get('field')!r}")
            if r.get("pipeline_stage", "") not in _VALID_STAGES:
                raise ValueError(f"{key}: invalid pipeline_stage {r.get('pipeline_stage')!r}")
            if r["rule_type"] == "regex":
                if not r.get("regex"):
                    raise ValueError(f"{key}: regex rule missing pattern")
                re.compile(r["regex"])  # raises on bad pattern
            else:  # keywords
                if not r.get("keywords"):
                    raise ValueError(f"{key}: keywords rule missing keywords")
            if r["action"] == "redact" and not r.get("replacement"):
                raise ValueError(f"{key}: redact rule needs a replacement token")
    for spec in _VECTOR_SPECS:
        key = spec["key"]
        if key in seen_keys:
            raise ValueError(f"duplicate vector key: {key}")
        seen_keys.add(key)
        if spec["vector_db_type"] not in _VALID_VECTOR_DB:
            raise ValueError(f"{key}: invalid vector_db_type")
        if spec["default_action"] not in _VALID_VECTOR_ACTION:
            raise ValueError(f"{key}: invalid default_action")
        for op in spec.get("allowed_operations") or []:
            if op not in _VALID_VECTOR_OPS:
                raise ValueError(f"{key}: invalid operation {op!r}")
        thr = spec.get("anomaly_distance_threshold", 0.85)
        if not (0.0 <= float(thr) <= 1.0):
            raise ValueError(f"{key}: anomaly threshold out of range")


_validate()


def policy_code(org_id: int, key: str) -> str:
    """Globally-unique, org-scoped policy code (Policy.code is unique)."""
    return f"PKG{org_id}_{key}"


def _condition_for_rule(rule: dict[str, Any], rule_key: str) -> dict[str, Any]:
    cond: dict[str, Any] = {
        "field": rule.get("field", "both"),
        "rule_key": rule_key,
        "package_id": PACKAGE_ID,
    }
    if rule["rule_type"] == "regex":
        cond["regex"] = rule["regex"]
    else:
        cond["keywords"] = list(rule["keywords"])
    return cond


def build_rule_dicts(policy_spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert a policy spec's rules into Rule-ready dicts (with stable rule_key)."""
    out: list[dict[str, Any]] = []
    rules = policy_spec.get("rules") or []
    total = len(rules)
    for idx, rule in enumerate(rules):
        rule_key = f"{policy_spec['key']}__{idx:02d}"
        out.append(
            {
                "name": rule["name"],
                "rule_type": rule["rule_type"],
                "condition": _condition_for_rule(rule, rule_key),
                "action": rule["action"],
                "redaction_config": (
                    {"replacement": rule["replacement"]}
                    if rule.get("action") == "redact" and rule.get("replacement")
                    else {}
                ),
                "priority": total - idx,
                "enabled": True,
                "pipeline_stage": rule.get("pipeline_stage", "") or "",
                "target_tool": rule.get("target_tool", "") or "",
                "description": rule.get("description", ""),
                "_rule_key": rule_key,
            }
        )
    return out


def policy_specs() -> list[dict[str, Any]]:
    return _POLICY_SPECS


def vector_specs() -> list[dict[str, Any]]:
    return _VECTOR_SPECS


def package_metadata() -> dict[str, Any]:
    by_domain: dict[str, int] = {}
    for s in _POLICY_SPECS:
        by_domain[s["domain"]] = by_domain.get(s["domain"], 0) + 1
    by_domain["vector"] = len(_VECTOR_SPECS)
    total_rules = sum(len(s.get("rules") or []) for s in _POLICY_SPECS)
    return {
        "package_id": PACKAGE_ID,
        "package_version": PACKAGE_VERSION,
        "policy_count": len(_POLICY_SPECS) + len(_VECTOR_SPECS),
        "rule_count": total_rules,
        "policies_by_domain": by_domain,
        "source": "policy.policy_package.catalog",
    }


if __name__ == "__main__":
    meta = package_metadata()
    print("Policy package:", meta["package_id"], "v" + meta["package_version"])
    print("  policies:", meta["policy_count"], "rules:", meta["rule_count"])
    print("  by domain:", meta["policies_by_domain"])
