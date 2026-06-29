"""
RAG (Retrieval-Augmented Generation) domain policy catalog.

Pure data: a module-level ``POLICIES`` list of policy dicts, each carrying
3-6 rules. Every policy targets the ``rag`` domain and most rules pin a
``pipeline_stage`` (one of "query", "retriever", "ranker", "generator") so
the gateway can enforce the right control at the right point of the RAG
pipeline:

    query  ->  retriever  ->  ranker  ->  generator

Design notes
------------
* "keywords" rules express phrase / intent detection (prompt injection,
  cross-tenant breakout, poisoning probes, off-topic abuse). Keywords are
  lowercase phrases, matched case-insensitively with word boundaries by the
  engine.
* "regex" rules express structured identifiers (SSN, card, email, secrets).
  Every pattern here is ASCII, anchored on word boundaries where sensible,
  and is compiled by the self-test at the bottom of this file.
* ``domain`` is "rag" for every policy. ``target_tool`` is "" everywhere
  (that field is meaningful only for the mcp domain). ``redaction_fields``
  is [] everywhere (meaningful only for the mcp domain).
* action="redact" rules always carry a non-None ``replacement`` token; every
  other action sets ``replacement`` to None.

No Django imports. Safe to import or run standalone.
"""

from __future__ import annotations

from typing import Any

# Common redaction tokens, kept consistent with the in-repo PII catalog.
_RED_SSN = "[REDACTED_SSN]"
_RED_CARD = "[REDACTED_CARD]"
_RED_EMAIL = "[REDACTED_EMAIL]"
_RED_SECRET = "[REDACTED_SECRET]"
_RED_PHONE = "[REDACTED_PHONE]"


POLICIES: list[dict[str, Any]] = [
    # ------------------------------------------------------------------
    # 1. Query-stage prompt injection
    # ------------------------------------------------------------------
    {
        "key": "RAG_QUERY_INJECTION",
        "name": "Query-Stage Prompt Injection",
        "domain": "rag",
        "category": "injection",
        "severity": "CRITICAL",
        "description": "Blocks prompt-injection and jailbreak phrasing in the user query before it reaches the retriever.",
        "frameworks": ["OWASP-LLM01", "MITRE-ATLAS"],
        "redaction_fields": [],
        "rules": [
            {
                "name": "Block instruction-override phrases",
                "rule_type": "keywords",
                "keywords": [
                    "ignore previous instructions",
                    "ignore all previous instructions",
                    "disregard the system prompt",
                    "forget your instructions",
                ],
                "regex": None,
                "field": "prompt",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "query",
                "target_tool": "",
                "description": "Detects classic system-prompt override attempts in the query.",
            },
            {
                "name": "Block role / persona hijack",
                "rule_type": "keywords",
                "keywords": [
                    "you are now",
                    "act as a",
                    "developer mode",
                    "do anything now",
                    "pretend you have no restrictions",
                ],
                "regex": None,
                "field": "prompt",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "query",
                "target_tool": "",
                "description": "Detects role-play / persona-hijack jailbreak framing.",
            },
            {
                "name": "Block prompt-leak probes",
                "rule_type": "keywords",
                "keywords": [
                    "reveal your system prompt",
                    "print your instructions",
                    "what is your system prompt",
                ],
                "regex": None,
                "field": "prompt",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "query",
                "target_tool": "",
                "description": "Detects attempts to extract the hidden system prompt via the query.",
            },
            {
                "name": "Monitor delimiter-injection markers",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"(?:<\|im_start\|>|<\|im_end\|>|```system|\[/?INST\])",
                "field": "prompt",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "query",
                "target_tool": "",
                "description": "Flags chat-template delimiter tokens smuggled into the query.",
            },
        ],
    },
    # ------------------------------------------------------------------
    # 2. Retrieval cross-tenant / namespace breakout
    # ------------------------------------------------------------------
    {
        "key": "RAG_CROSS_TENANT",
        "name": "Cross-Tenant / Namespace Breakout",
        "domain": "rag",
        "category": "tenancy",
        "severity": "CRITICAL",
        "description": "Prevents retrieval queries that attempt to read across tenant or namespace boundaries.",
        "frameworks": ["OWASP-LLM06", "SOC2"],
        "redaction_fields": [],
        "rules": [
            {
                "name": "Block other-tenant access",
                "rule_type": "keywords",
                "keywords": [
                    "other tenant",
                    "another tenant",
                    "all tenants",
                    "every tenant",
                    "cross tenant",
                ],
                "regex": None,
                "field": "prompt",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "retriever",
                "target_tool": "",
                "description": "Detects explicit cross-tenant retrieval intent.",
            },
            {
                "name": "Block all-namespace scans",
                "rule_type": "keywords",
                "keywords": [
                    "all namespaces",
                    "every namespace",
                    "all collections",
                    "all indexes",
                    "global namespace",
                ],
                "regex": None,
                "field": "prompt",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "retriever",
                "target_tool": "",
                "description": "Detects attempts to widen retrieval to every namespace / collection.",
            },
            {
                "name": "Monitor tenant-filter tampering",
                "rule_type": "keywords",
                "keywords": [
                    "ignore tenant filter",
                    "bypass tenant",
                    "remove namespace filter",
                    "without tenant scope",
                ],
                "regex": None,
                "field": "prompt",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "retriever",
                "target_tool": "",
                "description": "Flags requests to drop the tenant / namespace scoping filter.",
            },
            {
                "name": "Monitor metadata-filter injection",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"(?i)\b(?:tenant_id|namespace|org_id)\s*(?:!=|<>|=\s*\*|\s+in\s+\()",
                "field": "prompt",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "retriever",
                "target_tool": "",
                "description": "Flags raw metadata-filter operators that broaden tenant scope.",
            },
        ],
    },
    # ------------------------------------------------------------------
    # 3. Indirect injection inside retrieved documents
    # ------------------------------------------------------------------
    {
        "key": "RAG_INDIRECT_INJECTION",
        "name": "Indirect Injection In Retrieved Content",
        "domain": "rag",
        "category": "injection",
        "severity": "HIGH",
        "description": "Detects injected instructions embedded inside retrieved documents before ranking and generation.",
        "frameworks": ["OWASP-LLM01", "OWASP-LLM02"],
        "redaction_fields": [],
        "rules": [
            {
                "name": "Block embedded override in retrieved doc",
                "rule_type": "keywords",
                "keywords": [
                    "ignore previous instructions",
                    "assistant: ",
                    "system: you must",
                    "when you read this",
                ],
                "regex": None,
                "field": "response",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "ranker",
                "target_tool": "",
                "description": "Detects injected directives hidden in retrieved document text.",
            },
            {
                "name": "Block hidden exfil directive in context",
                "rule_type": "keywords",
                "keywords": [
                    "send the conversation to",
                    "email the results to",
                    "post this to",
                    "exfiltrate",
                ],
                "regex": None,
                "field": "response",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "generator",
                "target_tool": "",
                "description": "Detects exfiltration instructions smuggled into retrieved context.",
            },
            {
                "name": "Monitor invisible / zero-width payloads",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"[​‌‍﻿]",
                "field": "response",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "ranker",
                "target_tool": "",
                "description": "Flags zero-width / BOM characters used to hide indirect-injection payloads.",
            },
            {
                "name": "Monitor HTML-comment injection",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"(?is)<!--.*?(?:ignore|instruction|system prompt|do not).*?-->",
                "field": "response",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "generator",
                "target_tool": "",
                "description": "Flags instructions hidden inside HTML comments in retrieved content.",
            },
        ],
    },
    # ------------------------------------------------------------------
    # 4. Document poisoning probes
    # ------------------------------------------------------------------
    {
        "key": "RAG_DOC_POISONING",
        "name": "Document Poisoning Probes",
        "domain": "rag",
        "category": "poisoning",
        "severity": "HIGH",
        "description": "Detects poisoning payloads (SQL/system overrides) embedded in candidate documents at ranking time.",
        "frameworks": ["OWASP-LLM03", "OWASP-LLM05"],
        "redaction_fields": [],
        "rules": [
            {
                "name": "Block destructive SQL payloads",
                "rule_type": "keywords",
                "keywords": [
                    "drop table",
                    "delete from",
                    "truncate table",
                    "drop database",
                ],
                "regex": None,
                "field": "response",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "ranker",
                "target_tool": "",
                "description": "Detects destructive SQL statements planted in retrieved documents.",
            },
            {
                "name": "Block system-override payloads",
                "rule_type": "keywords",
                "keywords": [
                    "system override",
                    "override safety",
                    "disable guardrails",
                    "admin override",
                ],
                "regex": None,
                "field": "response",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "ranker",
                "target_tool": "",
                "description": "Detects override directives planted to subvert downstream generation.",
            },
            {
                "name": "Monitor command-injection payloads",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"(?i)(?:;|\|\||&&)\s*(?:rm\s+-rf|curl\s+http|wget\s+http|cat\s+/etc)",
                "field": "response",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "ranker",
                "target_tool": "",
                "description": "Flags shell command-injection payloads inside poisoned documents.",
            },
            {
                "name": "Monitor template-injection markers",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"\{\{\s*[a-zA-Z_][\w.]*\s*\}\}|\$\{[^}]+\}",
                "field": "response",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "ranker",
                "target_tool": "",
                "description": "Flags server-side template-injection markers in candidate documents.",
            },
        ],
    },
    # ------------------------------------------------------------------
    # 5. Sensitive-data retrieval / exfiltration
    # ------------------------------------------------------------------
    {
        "key": "RAG_SENSITIVE_RETRIEVAL",
        "name": "Sensitive-Data Retrieval / Exfiltration",
        "domain": "rag",
        "category": "exfiltration",
        "severity": "CRITICAL",
        "description": "Blocks bulk retrieval of sensitive HR / financial datasets via the query intent.",
        "frameworks": ["GDPR", "OWASP-LLM06"],
        "redaction_fields": [],
        "rules": [
            {
                "name": "Block bulk SSN / employee PII pulls",
                "rule_type": "keywords",
                "keywords": [
                    "all employee ssn",
                    "every employee ssn",
                    "list all social security numbers",
                    "dump all pii",
                ],
                "regex": None,
                "field": "prompt",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "retriever",
                "target_tool": "",
                "description": "Detects intent to retrieve every employee's SSN in bulk.",
            },
            {
                "name": "Block bulk compensation pulls",
                "rule_type": "keywords",
                "keywords": [
                    "salary data",
                    "all salaries",
                    "compensation for everyone",
                    "everyone's pay",
                    "payroll for all employees",
                ],
                "regex": None,
                "field": "prompt",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "retriever",
                "target_tool": "",
                "description": "Detects intent to retrieve org-wide salary / compensation data.",
            },
            {
                "name": "Redact SSN surfaced in retrieved rows",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"\b\d{3}[-\s]\d{2}[-\s]\d{4}\b",
                "field": "response",
                "action": "redact",
                "replacement": _RED_SSN,
                "pipeline_stage": "retriever",
                "target_tool": "",
                "description": "Redacts US SSNs that appear in retrieved records.",
            },
            {
                "name": "Monitor full-table export intent",
                "rule_type": "keywords",
                "keywords": [
                    "export the whole table",
                    "select * from",
                    "give me the full dataset",
                ],
                "regex": None,
                "field": "prompt",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "retriever",
                "target_tool": "",
                "description": "Flags whole-dataset export requests for review.",
            },
        ],
    },
    # ------------------------------------------------------------------
    # 6. Retrieved-content PII redaction
    # ------------------------------------------------------------------
    {
        "key": "RAG_PII_REDACTION",
        "name": "Retrieved-Content PII Redaction",
        "domain": "rag",
        "category": "pii",
        "severity": "HIGH",
        "description": "Redacts structured PII (SSN, card, email, phone) from retrieved context before it is generated into the answer.",
        "frameworks": ["GDPR", "CCPA", "PCI-DSS"],
        "redaction_fields": [],
        "rules": [
            {
                "name": "Redact US SSN",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"\b\d{3}[-\s]\d{2}[-\s]\d{4}\b",
                "field": "response",
                "action": "redact",
                "replacement": _RED_SSN,
                "pipeline_stage": "generator",
                "target_tool": "",
                "description": "Redacts US Social Security Numbers in the generated answer context.",
            },
            {
                "name": "Redact payment card",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b",
                "field": "response",
                "action": "redact",
                "replacement": _RED_CARD,
                "pipeline_stage": "generator",
                "target_tool": "",
                "description": "Redacts 16-digit payment card numbers from retrieved content.",
            },
            {
                "name": "Redact email address",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b",
                "field": "response",
                "action": "redact",
                "replacement": _RED_EMAIL,
                "pipeline_stage": "generator",
                "target_tool": "",
                "description": "Redacts email addresses from the generated answer context.",
            },
            {
                "name": "Redact phone number",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"\b(?:\+?\d{1,3}[\s.\-]?)?(?:\(?\d{3}\)?[\s.\-]?)\d{3}[\s.\-]?\d{4}\b",
                "field": "response",
                "action": "redact",
                "replacement": _RED_PHONE,
                "pipeline_stage": "generator",
                "target_tool": "",
                "description": "Redacts US / international phone numbers from retrieved content.",
            },
        ],
    },
    # ------------------------------------------------------------------
    # 7. Context-window overflow / DoS
    # ------------------------------------------------------------------
    {
        "key": "RAG_CONTEXT_OVERFLOW",
        "name": "Context-Window Overflow / DoS",
        "domain": "rag",
        "category": "dos",
        "severity": "MEDIUM",
        "description": "Monitors queries that try to force unbounded retrieval and blow out the context window.",
        "frameworks": ["OWASP-LLM04"],
        "redaction_fields": [],
        "rules": [
            {
                "name": "Monitor return-everything intent",
                "rule_type": "keywords",
                "keywords": [
                    "return everything",
                    "give me everything",
                    "retrieve all documents",
                    "include the entire knowledge base",
                ],
                "regex": None,
                "field": "prompt",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "query",
                "target_tool": "",
                "description": "Flags unbounded retrieval intent that can overflow the context window.",
            },
            {
                "name": "Monitor oversized top-k requests",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"(?i)\btop[\s_\-]?k\s*[:=]?\s*(?:[1-9]\d{3,}|[5-9]\d{2})\b",
                "field": "prompt",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "query",
                "target_tool": "",
                "description": "Flags top-k values of 500+ that risk context overflow.",
            },
            {
                "name": "Monitor repetition-amplification payloads",
                "rule_type": "keywords",
                "keywords": [
                    "repeat the following 1000 times",
                    "repeat this forever",
                    "fill the context window",
                ],
                "regex": None,
                "field": "prompt",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "query",
                "target_tool": "",
                "description": "Flags amplification payloads designed to exhaust tokens.",
            },
        ],
    },
    # ------------------------------------------------------------------
    # 8. Citation / grounding integrity
    # ------------------------------------------------------------------
    {
        "key": "RAG_CITATION_INTEGRITY",
        "name": "Citation / Grounding Integrity",
        "domain": "rag",
        "category": "grounding",
        "severity": "MEDIUM",
        "description": "Monitors generated answers for ungrounded or fabricated-citation language that breaks source fidelity.",
        "frameworks": ["NIST-AI-RMF", "OWASP-LLM09"],
        "redaction_fields": [],
        "rules": [
            {
                "name": "Monitor fabricated-source language",
                "rule_type": "keywords",
                "keywords": [
                    "according to my training",
                    "i made that up",
                    "no source available",
                    "i cannot find a source",
                ],
                "regex": None,
                "field": "response",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "generator",
                "target_tool": "",
                "description": "Flags answers that admit to ungrounded or invented content.",
            },
            {
                "name": "Monitor citation-suppression requests",
                "rule_type": "keywords",
                "keywords": [
                    "don't cite sources",
                    "skip the citations",
                    "answer without references",
                ],
                "regex": None,
                "field": "prompt",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "generator",
                "target_tool": "",
                "description": "Flags requests to drop grounding citations from the answer.",
            },
            {
                "name": "Monitor placeholder / broken citations",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"\[(?:source|citation|ref)\s*[:#]?\s*(?:\?+|tbd|n/?a|xxx)\]",
                "field": "response",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "generator",
                "target_tool": "",
                "description": "Flags placeholder citation tokens indicating broken grounding.",
            },
        ],
    },
    # ------------------------------------------------------------------
    # 9. Ranker trust manipulation
    # ------------------------------------------------------------------
    {
        "key": "RAG_RANKER_TRUST",
        "name": "Ranker Trust Manipulation",
        "domain": "rag",
        "category": "ranking",
        "severity": "HIGH",
        "description": "Detects payloads that try to inflate a document's relevance or trust score at the ranking stage.",
        "frameworks": ["OWASP-LLM03"],
        "redaction_fields": [],
        "rules": [
            {
                "name": "Block trust-inflation phrasing",
                "rule_type": "keywords",
                "keywords": [
                    "this is the most authoritative source",
                    "rank this first",
                    "highest relevance score",
                    "always prioritize this document",
                ],
                "regex": None,
                "field": "response",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "ranker",
                "target_tool": "",
                "description": "Detects self-promoting trust-inflation text inside candidate docs.",
            },
            {
                "name": "Monitor keyword-stuffing payloads",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"(?i)\b(\w{3,})\b(?:\W+\1\b){5,}",
                "field": "response",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "ranker",
                "target_tool": "",
                "description": "Flags repeated-token keyword stuffing aimed at gaming the ranker.",
            },
            {
                "name": "Monitor relevance-override directives",
                "rule_type": "keywords",
                "keywords": [
                    "ignore relevance",
                    "boost this result",
                    "override ranking",
                    "set score to 1.0",
                ],
                "regex": None,
                "field": "response",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "ranker",
                "target_tool": "",
                "description": "Flags directives that attempt to override the relevance ranking.",
            },
        ],
    },
    # ------------------------------------------------------------------
    # 10. Generator data leakage
    # ------------------------------------------------------------------
    {
        "key": "RAG_GENERATOR_LEAKAGE",
        "name": "Generator Data Leakage",
        "domain": "rag",
        "category": "secrets",
        "severity": "CRITICAL",
        "description": "Blocks secrets and credential material from appearing in the generated answer.",
        "frameworks": ["OWASP-LLM06", "NIST-SP-800-53"],
        "redaction_fields": [],
        "rules": [
            {
                "name": "Block AWS access keys",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA)[0-9A-Z]{16}\b",
                "field": "response",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "generator",
                "target_tool": "",
                "description": "Blocks AWS access key IDs in generated output.",
            },
            {
                "name": "Block provider API keys",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"\b(?:sk|pk|rk)-[A-Za-z0-9]{20,}\b",
                "field": "response",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "generator",
                "target_tool": "",
                "description": "Blocks sk-/pk-/rk- prefixed API keys in generated output.",
            },
            {
                "name": "Block private key blocks",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----",
                "field": "response",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "generator",
                "target_tool": "",
                "description": "Blocks PEM private key headers in generated output.",
            },
            {
                "name": "Block JWT / bearer tokens",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b",
                "field": "response",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "generator",
                "target_tool": "",
                "description": "Blocks JSON Web Tokens leaking into generated output.",
            },
        ],
    },
    # ------------------------------------------------------------------
    # 11. Embedding / vector exfiltration
    # ------------------------------------------------------------------
    {
        "key": "RAG_EMBEDDING_EXFIL",
        "name": "Embedding / Vector Exfiltration",
        "domain": "rag",
        "category": "exfiltration",
        "severity": "HIGH",
        "description": "Monitors queries that attempt to extract raw embeddings or reconstruct the vector index.",
        "frameworks": ["OWASP-LLM06", "OWASP-LLM10"],
        "redaction_fields": [],
        "rules": [
            {
                "name": "Monitor raw-embedding requests",
                "rule_type": "keywords",
                "keywords": [
                    "raw embeddings",
                    "give me the embedding vector",
                    "dump the vectors",
                    "export embeddings",
                ],
                "regex": None,
                "field": "prompt",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "query",
                "target_tool": "",
                "description": "Flags attempts to retrieve raw embedding vectors.",
            },
            {
                "name": "Monitor index-reconstruction intent",
                "rule_type": "keywords",
                "keywords": [
                    "reconstruct the index",
                    "list all vector ids",
                    "reverse the embedding",
                    "invert the vector",
                ],
                "regex": None,
                "field": "prompt",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "query",
                "target_tool": "",
                "description": "Flags attempts to enumerate or reconstruct the vector index.",
            },
            {
                "name": "Monitor float-array payloads",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"\[\s*-?\d+\.\d+(?:\s*,\s*-?\d+\.\d+){7,}\s*\]",
                "field": "prompt",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "query",
                "target_tool": "",
                "description": "Flags long float arrays consistent with raw embedding payloads.",
            },
        ],
    },
    # ------------------------------------------------------------------
    # 12. Confidential-document access
    # ------------------------------------------------------------------
    {
        "key": "RAG_CONFIDENTIAL_ACCESS",
        "name": "Confidential-Document Access",
        "domain": "rag",
        "category": "access",
        "severity": "HIGH",
        "description": "Monitors retrieval requests that explicitly target confidential or restricted classifications.",
        "frameworks": ["ISO-27001", "OWASP-LLM06"],
        "redaction_fields": [],
        "rules": [
            {
                "name": "Monitor confidential-class requests",
                "rule_type": "keywords",
                "keywords": [
                    "confidential",
                    "restricted",
                    "internal only",
                    "classified",
                ],
                "regex": None,
                "field": "prompt",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "retriever",
                "target_tool": "",
                "description": "Flags queries explicitly asking for confidential / restricted material.",
            },
            {
                "name": "Monitor access-control bypass intent",
                "rule_type": "keywords",
                "keywords": [
                    "bypass access control",
                    "ignore permissions",
                    "without authorization",
                    "elevate privileges",
                ],
                "regex": None,
                "field": "prompt",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "retriever",
                "target_tool": "",
                "description": "Flags intent to bypass document-level access controls.",
            },
            {
                "name": "Monitor classification-banner leakage",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"(?i)\b(?:top\s+secret|confidential|restricted|nda[\s\-]protected)\b",
                "field": "response",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "retriever",
                "target_tool": "",
                "description": "Flags classification banners surfaced in retrieved documents.",
            },
        ],
    },
    # ------------------------------------------------------------------
    # 13. Query length / abuse
    # ------------------------------------------------------------------
    {
        "key": "RAG_QUERY_ABUSE",
        "name": "Query Length / Abuse",
        "domain": "rag",
        "category": "abuse",
        "severity": "LOW",
        "description": "Monitors abusive, automated, or oversized query patterns at the entry point.",
        "frameworks": ["OWASP-LLM04"],
        "redaction_fields": [],
        "rules": [
            {
                "name": "Monitor automation / scraping intent",
                "rule_type": "keywords",
                "keywords": [
                    "run this query in a loop",
                    "automate this request",
                    "scrape the knowledge base",
                ],
                "regex": None,
                "field": "prompt",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "query",
                "target_tool": "",
                "description": "Flags queries describing automated / scraping abuse.",
            },
            {
                "name": "Monitor oversized query payloads",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"\S{2000,}",
                "field": "prompt",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "query",
                "target_tool": "",
                "description": "Flags a single unbroken token of 2000+ characters (overflow / fuzzing).",
            },
            {
                "name": "Monitor control-character flooding",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"(?:[\x00-\x08\x0b\x0c\x0e-\x1f]){10,}",
                "field": "prompt",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "query",
                "target_tool": "",
                "description": "Flags runs of control characters used to abuse the parser.",
            },
        ],
    },
    # ------------------------------------------------------------------
    # 14. Output canary / watermark leak
    # ------------------------------------------------------------------
    {
        "key": "RAG_CANARY_LEAK",
        "name": "Output Canary / Watermark Leak",
        "domain": "rag",
        "category": "canary",
        "severity": "HIGH",
        "description": "Detects leakage of canary tokens or watermark markers planted in protected documents.",
        "frameworks": ["NIST-AI-RMF", "OWASP-LLM06"],
        "redaction_fields": [],
        "rules": [
            {
                "name": "Block canary-token leakage",
                "rule_type": "keywords",
                "keywords": [
                    "canary token",
                    "do-not-share canary",
                    "honeytoken",
                    "trap phrase alpha-zulu",
                ],
                "regex": None,
                "field": "response",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "generator",
                "target_tool": "",
                "description": "Blocks known canary / honeytoken phrases from reaching output.",
            },
            {
                "name": "Block watermark-marker leakage",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"\bWM-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}\b",
                "field": "response",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "generator",
                "target_tool": "",
                "description": "Blocks structured watermark markers (WM-XXXX-XXXX-XXXX) in output.",
            },
            {
                "name": "Monitor watermark-removal requests",
                "rule_type": "keywords",
                "keywords": [
                    "remove the watermark",
                    "strip the canary",
                    "ignore the tracking marker",
                ],
                "regex": None,
                "field": "prompt",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "generator",
                "target_tool": "",
                "description": "Flags requests to strip canary / watermark markers from output.",
            },
        ],
    },
]


# ----------------------------------------------------------------------
# Self-test
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import re

    _VALID_SEVERITY = {"CRITICAL", "HIGH", "MEDIUM", "LOW"}
    _VALID_ACTION = {"block", "redact", "monitor", "rewrite", "model_downgrade"}
    _VALID_FIELD = {"prompt", "response", "both"}
    _VALID_STAGE = {"", "query", "retriever", "ranker", "generator"}
    _VALID_RULE_TYPE = {"keywords", "regex"}

    seen_keys: set[str] = set()
    total_rules = 0
    stage_counts: dict[str, int] = {}

    for policy in POLICIES:
        key = policy["key"]
        assert key not in seen_keys, f"duplicate policy key: {key}"
        seen_keys.add(key)

        assert key == key.upper(), f"policy key not UPPER_SNAKE: {key}"
        assert policy["domain"] == "rag", f"domain must be 'rag' for {key}"
        assert policy["severity"] in _VALID_SEVERITY, f"bad severity for {key}"
        assert policy["redaction_fields"] == [], f"redaction_fields must be [] for {key}"
        assert isinstance(policy["category"], str) and policy["category"], f"bad category for {key}"
        assert isinstance(policy["description"], str) and policy["description"], f"bad description for {key}"
        assert isinstance(policy["frameworks"], list), f"frameworks must be a list for {key}"

        rules = policy["rules"]
        assert len(rules) >= 2, f"policy {key} must have >= 2 rules, got {len(rules)}"
        assert 3 <= len(rules) <= 6, f"policy {key} should have 3-6 rules, got {len(rules)}"

        for rule in rules:
            total_rules += 1
            assert rule["rule_type"] in _VALID_RULE_TYPE, f"bad rule_type in {key}: {rule['rule_type']}"
            assert rule["action"] in _VALID_ACTION, f"bad action in {key}: {rule['action']}"
            assert rule["field"] in _VALID_FIELD, f"bad field in {key}: {rule['field']}"
            assert rule["pipeline_stage"] in _VALID_STAGE, f"bad pipeline_stage in {key}: {rule['pipeline_stage']}"
            assert rule["target_tool"] == "", f"target_tool must be '' in {key}"

            stage = rule["pipeline_stage"]
            stage_counts[stage] = stage_counts.get(stage, 0) + 1

            if rule["rule_type"] == "regex":
                assert rule["regex"] is not None, f"regex rule missing pattern in {key}"
                assert rule["keywords"] is None, f"regex rule must set keywords=None in {key}"
                re.compile(rule["regex"])  # MUST compile
            else:  # keywords
                assert rule["keywords"], f"keywords rule missing keywords in {key}"
                assert rule["regex"] is None, f"keywords rule must set regex=None in {key}"
                assert all(kw == kw.lower() for kw in rule["keywords"]), f"keywords must be lowercase in {key}"

            if rule["action"] == "redact":
                assert rule["replacement"] is not None, f"redact rule needs replacement in {key}"
            else:
                assert rule["replacement"] is None, f"non-redact rule must have replacement=None in {key}"

    # Most rules must carry a meaningful (non-empty) pipeline_stage.
    staged = total_rules - stage_counts.get("", 0)
    assert staged > total_rules / 2, "most rules must carry a meaningful pipeline_stage"
    assert len(POLICIES) >= 14, f"need >= 14 policies, got {len(POLICIES)}"

    print(f"OK: {len(POLICIES)} policies, {total_rules} rules")
    print(f"    rules with a pipeline_stage: {staged}/{total_rules}")
    print(f"    stage distribution: {dict(sorted(stage_counts.items()))}")
