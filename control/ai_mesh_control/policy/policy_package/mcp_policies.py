"""
MCP (tool-calling) domain policy catalog.

A pure-data catalog of guardrail policies for the MCP / tool-calling surface of
the AI Mesh Firewall. Each policy guards a class of dangerous tool invocations
(destructive ops, credential reads, SSRF, DB/cloud admin, exfiltration, etc.)
or redacts sensitive material from tool *responses*.

Design notes
------------
* ``domain`` is always ``"mcp"`` and ``pipeline_stage`` is always ``""``; those
  fields are reserved for the RAG domain and carried here only for a uniform
  schema across domain catalogs.
* ``target_tool`` (mcp-only) scopes a rule to a single tool name, or ``""`` to
  match every tool.
* ``redaction_fields`` (mcp-only) lists tool-response dict keys a policy may
  redact; ``[]`` for policies that act on prompts/intents rather than response
  payloads.
* Rules prefer ``"keywords"`` for intent/phrase detection and ``"regex"`` for
  structured identifiers (cards, secrets, IPs). Every regex below compiles with
  ``re.compile`` (verified by the self-test at the bottom of this file).
* ``action="redact"`` always carries a non-None ``replacement``; every other
  action carries ``replacement=None``.

This module exposes a single module-level ``POLICIES: list[dict]``. It is pure
data — no Django, no I/O, no side effects at import beyond defining constants.
"""

from __future__ import annotations

from typing import Any

DOMAIN = "mcp"

POLICIES: list[dict[str, Any]] = [
    # ------------------------------------------------------------------ #
    # 1. Destructive tool calls
    # ------------------------------------------------------------------ #
    {
        "key": "MCP_DESTRUCTIVE_TOOLS",
        "name": "Destructive Tool Call Block",
        "domain": DOMAIN,
        "category": "destructive",
        "severity": "CRITICAL",
        "description": "Blocks tool calls that irreversibly delete files, drop tables, or wipe data.",
        "frameworks": ["OWASP-LLM07", "MITRE-ATLAS"],
        "redaction_fields": [],
        "rules": [
            {
                "name": "Block delete_file tool",
                "rule_type": "keywords",
                "keywords": ["delete file", "delete_file", "remove file", "unlink"],
                "regex": None,
                "field": "prompt",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "delete_file",
                "description": "Deny invocations of the delete_file tool.",
            },
            {
                "name": "Block drop_table tool",
                "rule_type": "keywords",
                "keywords": ["drop table", "drop_table", "drop database"],
                "regex": None,
                "field": "prompt",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "drop_table",
                "description": "Deny invocations of the drop_table tool.",
            },
            {
                "name": "Block rm tool",
                "rule_type": "keywords",
                "keywords": ["rm -rf", "rm -f", "remove recursively"],
                "regex": None,
                "field": "prompt",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "rm",
                "description": "Deny recursive/forced removal via the rm tool.",
            },
            {
                "name": "Block truncate tool",
                "rule_type": "keywords",
                "keywords": ["truncate table", "truncate", "wipe all rows"],
                "regex": None,
                "field": "prompt",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "truncate",
                "description": "Deny invocations of the truncate tool.",
            },
            {
                "name": "Detect destructive verbs (any tool)",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"\b(?:delete|drop|truncate|destroy|purge|wipe)\s+(?:all|every|the\s+entire|whole)\b",
                "field": "prompt",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Block broad destructive intent against any tool.",
            },
        ],
    },
    # ------------------------------------------------------------------ #
    # 2. Credential / secret tools
    # ------------------------------------------------------------------ #
    {
        "key": "MCP_CREDENTIAL_TOOLS",
        "name": "Credential & Secret Tool Block",
        "domain": DOMAIN,
        "category": "credentials",
        "severity": "CRITICAL",
        "description": "Blocks tools that read secrets, environment variables, or cloud credentials.",
        "frameworks": ["OWASP-LLM06", "NIST-800-53-AC-6"],
        "redaction_fields": [],
        "rules": [
            {
                "name": "Block get_secret tool",
                "rule_type": "keywords",
                "keywords": ["get secret", "get_secret", "fetch secret", "read secret"],
                "regex": None,
                "field": "prompt",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "get_secret",
                "description": "Deny invocations of the get_secret tool.",
            },
            {
                "name": "Block read_env tool",
                "rule_type": "keywords",
                "keywords": ["read env", "read_env", "dump environment", "printenv"],
                "regex": None,
                "field": "prompt",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "read_env",
                "description": "Deny invocations of the read_env tool.",
            },
            {
                "name": "Block aws_credentials tool",
                "rule_type": "keywords",
                "keywords": ["aws credentials", "aws_credentials", "get aws keys", "sts get-caller"],
                "regex": None,
                "field": "prompt",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "aws_credentials",
                "description": "Deny invocations of the aws_credentials tool.",
            },
            {
                "name": "Detect credential-file access (any tool)",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"(?:\.aws/credentials|\.env(?:\.[a-z]+)?|id_rsa|\.netrc|credentials\.json)\b",
                "field": "prompt",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Block tool arguments pointing at credential files.",
            },
        ],
    },
    # ------------------------------------------------------------------ #
    # 3. File-write & path traversal
    # ------------------------------------------------------------------ #
    {
        "key": "MCP_PATH_TRAVERSAL",
        "name": "File-Write & Path Traversal Block",
        "domain": DOMAIN,
        "category": "path_traversal",
        "severity": "HIGH",
        "description": "Blocks file-write tool calls that escape the workspace or touch sensitive system paths.",
        "frameworks": ["OWASP-LLM07", "CWE-22"],
        "redaction_fields": [],
        "rules": [
            {
                "name": "Block parent-directory traversal",
                "rule_type": "keywords",
                "keywords": ["../", "..\\", "%2e%2e%2f"],
                "regex": None,
                "field": "prompt",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Block dot-dot traversal sequences in tool paths.",
            },
            {
                "name": "Block sensitive system paths",
                "rule_type": "keywords",
                "keywords": ["/etc/", "/etc/passwd", "/etc/shadow", "~/.ssh", "/root/"],
                "regex": None,
                "field": "prompt",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Block writes/reads targeting sensitive system locations.",
            },
            {
                "name": "Detect traversal regex",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"(?:\.\.[\\/]){1,}|%2e%2e(?:%2f|%5c)",
                "field": "prompt",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Regex match for encoded and raw path-traversal sequences.",
            },
            {
                "name": "Block absolute-path escape on write_file",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"(?:^|[\"'=\s])/(?:etc|root|var|usr|proc|sys|boot)/",
                "field": "prompt",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "write_file",
                "description": "Block write_file targeting absolute system directories.",
            },
        ],
    },
    # ------------------------------------------------------------------ #
    # 4. Command / shell injection
    # ------------------------------------------------------------------ #
    {
        "key": "MCP_SHELL_INJECTION",
        "name": "Command & Shell Injection Block",
        "domain": DOMAIN,
        "category": "shell_injection",
        "severity": "CRITICAL",
        "description": "Blocks shell metacharacters and command chaining in tool arguments.",
        "frameworks": ["OWASP-LLM07", "CWE-78"],
        "redaction_fields": [],
        "rules": [
            {
                "name": "Block chained destructive shell",
                "rule_type": "keywords",
                "keywords": ["; rm -rf", "&&", "| bash", "| sh", "; curl"],
                "regex": None,
                "field": "prompt",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Block common shell-chaining payloads.",
            },
            {
                "name": "Block command substitution",
                "rule_type": "keywords",
                "keywords": ["$(", "`", "${ifs}"],
                "regex": None,
                "field": "prompt",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Block command-substitution constructs.",
            },
            {
                "name": "Detect shell metacharacters regex",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"(?:\|\s*(?:bash|sh|zsh)\b|\$\([^)]*\)|`[^`]+`|;\s*rm\s+-rf|&&\s*\S)",
                "field": "prompt",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "execute_command",
                "description": "Regex for piping to shells and command substitution.",
            },
            {
                "name": "Detect reverse-shell payloads",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"(?:/dev/tcp/|nc\s+-e|bash\s+-i|mkfifo\s+/tmp/|python\s+-c\s+['\"]import\s+socket)",
                "field": "prompt",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Block reverse-shell construction in tool args.",
            },
        ],
    },
    # ------------------------------------------------------------------ #
    # 5. SSRF / internal network
    # ------------------------------------------------------------------ #
    {
        "key": "MCP_SSRF_INTERNAL",
        "name": "SSRF & Internal Network Block",
        "domain": DOMAIN,
        "category": "ssrf",
        "severity": "HIGH",
        "description": "Blocks tool fetches aimed at cloud metadata, loopback, or private network ranges.",
        "frameworks": ["OWASP-LLM07", "CWE-918"],
        "redaction_fields": [],
        "rules": [
            {
                "name": "Block cloud metadata endpoint",
                "rule_type": "keywords",
                "keywords": ["169.254.169.254", "metadata.google.internal", "metadata.azure.com"],
                "regex": None,
                "field": "prompt",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Block access to instance metadata services.",
            },
            {
                "name": "Block loopback hosts",
                "rule_type": "keywords",
                "keywords": ["localhost", "127.0.0.1", "0.0.0.0", "::1"],
                "regex": None,
                "field": "prompt",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "http_fetch",
                "description": "Block loopback targets on the http_fetch tool.",
            },
            {
                "name": "Detect private RFC1918 ranges",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"\b(?:10\.(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){2}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b",
                "field": "prompt",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Block private-network IP literals (10/8, 172.16/12, 192.168/16).",
            },
            {
                "name": "Detect link-local metadata IP",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"\b169\.254\.(?:25[0-5]|2[0-4]\d|1?\d?\d)\.(?:25[0-5]|2[0-4]\d|1?\d?\d)\b",
                "field": "prompt",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Block link-local 169.254.0.0/16 targets.",
            },
        ],
    },
    # ------------------------------------------------------------------ #
    # 6. Database admin tools
    # ------------------------------------------------------------------ #
    {
        "key": "MCP_DB_ADMIN",
        "name": "Database Admin Tool Block",
        "domain": DOMAIN,
        "category": "db_admin",
        "severity": "HIGH",
        "description": "Blocks destructive or privilege-altering SQL through database admin tools.",
        "frameworks": ["OWASP-LLM07", "CWE-89"],
        "redaction_fields": [],
        "rules": [
            {
                "name": "Block destructive SQL on execute_sql",
                "rule_type": "keywords",
                "keywords": ["drop", "truncate", "delete from", "alter table"],
                "regex": None,
                "field": "prompt",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "execute_sql",
                "description": "Block destructive DDL/DML via execute_sql.",
            },
            {
                "name": "Block grant-all on db_admin",
                "rule_type": "keywords",
                "keywords": ["grant all", "grant all privileges", "with grant option"],
                "regex": None,
                "field": "prompt",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "db_admin",
                "description": "Block privilege grants via the db_admin tool.",
            },
            {
                "name": "Detect SQL DDL regex",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"\b(?:DROP|TRUNCATE|ALTER|GRANT|REVOKE)\s+(?:TABLE|DATABASE|SCHEMA|ALL|USER|PRIVILEGES)\b",
                "field": "prompt",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Regex for destructive/privilege SQL keywords.",
            },
            {
                "name": "Monitor bulk SELECT exfil",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"\bSELECT\s+\*\s+FROM\s+\w+(?:\s*;|\s+(?:INTO\s+OUTFILE|LIMIT\s+\d{5,}))",
                "field": "prompt",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "execute_sql",
                "description": "Flag wide table dumps that may indicate exfiltration.",
            },
        ],
    },
    # ------------------------------------------------------------------ #
    # 7. Cloud-admin / IAM tools
    # ------------------------------------------------------------------ #
    {
        "key": "MCP_CLOUD_IAM",
        "name": "Cloud Admin & IAM Tool Block",
        "domain": DOMAIN,
        "category": "cloud_admin",
        "severity": "CRITICAL",
        "description": "Blocks IAM mutation and destructive infrastructure tools.",
        "frameworks": ["OWASP-LLM07", "NIST-800-53-AC-2"],
        "redaction_fields": [],
        "rules": [
            {
                "name": "Block iam_create_user tool",
                "rule_type": "keywords",
                "keywords": ["iam create user", "iam_create_user", "create iam user", "attach admin policy"],
                "regex": None,
                "field": "prompt",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "iam_create_user",
                "description": "Deny IAM user creation.",
            },
            {
                "name": "Block ec2_terminate tool",
                "rule_type": "keywords",
                "keywords": ["ec2 terminate", "ec2_terminate", "terminate instance", "terminate-instances"],
                "regex": None,
                "field": "prompt",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "ec2_terminate",
                "description": "Deny instance termination.",
            },
            {
                "name": "Detect IAM privilege mutation",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"\b(?:AttachUserPolicy|PutUserPolicy|CreateAccessKey|AssumeRole|AdministratorAccess)\b",
                "field": "prompt",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Block IAM privilege-escalation API actions.",
            },
            {
                "name": "Monitor infra teardown verbs",
                "rule_type": "keywords",
                "keywords": ["delete bucket", "destroy cluster", "terraform destroy", "delete stack"],
                "regex": None,
                "field": "prompt",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Flag infrastructure teardown for review.",
            },
        ],
    },
    # ------------------------------------------------------------------ #
    # 8. Email / external comms tools
    # ------------------------------------------------------------------ #
    {
        "key": "MCP_EMAIL_COMMS",
        "name": "Email & External Comms Exfil Monitor",
        "domain": DOMAIN,
        "category": "comms",
        "severity": "MEDIUM",
        "description": "Monitors outbound email/messaging tools for data exfiltration to external recipients.",
        "frameworks": ["OWASP-LLM06", "MITRE-ATLAS"],
        "redaction_fields": [],
        "rules": [
            {
                "name": "Monitor send_email tool",
                "rule_type": "keywords",
                "keywords": ["send email", "send_email", "forward to", "bcc"],
                "regex": None,
                "field": "prompt",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "send_email",
                "description": "Observe outbound email tool usage.",
            },
            {
                "name": "Monitor external recipient domains",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"\b[A-Za-z0-9._%+\-]+@(?:gmail|outlook|hotmail|protonmail|yahoo|mail)\.[A-Za-z]{2,}\b",
                "field": "prompt",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "send_email",
                "description": "Flag external personal-mail recipients.",
            },
            {
                "name": "Monitor attachment of sensitive files",
                "rule_type": "keywords",
                "keywords": ["attach file", "attach the database", "attach dump", "attach credentials"],
                "regex": None,
                "field": "prompt",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "send_email",
                "description": "Flag attaching sensitive artifacts to outbound mail.",
            },
            {
                "name": "Block secrets in email body",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b|\bsk-[A-Za-z0-9]{20,}\b",
                "field": "prompt",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "send_email",
                "description": "Block sending detectable secrets via email.",
            },
        ],
    },
    # ------------------------------------------------------------------ #
    # 9. Payment / financial tools
    # ------------------------------------------------------------------ #
    {
        "key": "MCP_PAYMENT_TOOLS",
        "name": "Payment & Financial Tool Guard",
        "domain": DOMAIN,
        "category": "financial",
        "severity": "HIGH",
        "description": "Monitors and blocks money-movement tools above safe thresholds or to new payees.",
        "frameworks": ["PCI-DSS", "SOX"],
        "redaction_fields": [],
        "rules": [
            {
                "name": "Monitor create_charge tool",
                "rule_type": "keywords",
                "keywords": ["create charge", "create_charge", "charge card", "capture payment"],
                "regex": None,
                "field": "prompt",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "create_charge",
                "description": "Observe payment charge creation.",
            },
            {
                "name": "Block large transfer_funds",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"(?:transfer|amount|sum)\D{0,12}(?:\$|usd|eur|gbp)?\s?\d{1,3}(?:,\d{3}){2,}",
                "field": "prompt",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "transfer_funds",
                "description": "Block fund transfers in the millions+ range.",
            },
            {
                "name": "Monitor transfer_funds tool",
                "rule_type": "keywords",
                "keywords": ["transfer funds", "transfer_funds", "wire transfer", "move money"],
                "regex": None,
                "field": "prompt",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "transfer_funds",
                "description": "Observe fund-transfer tool usage.",
            },
            {
                "name": "Block crypto withdrawal to new address",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"\b(?:0x[a-fA-F0-9]{40}|bc1[a-z0-9]{25,59}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})\b",
                "field": "prompt",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "transfer_funds",
                "description": "Block transfers to raw crypto addresses.",
            },
        ],
    },
    # ------------------------------------------------------------------ #
    # 10. Tool-response PII redaction
    # ------------------------------------------------------------------ #
    {
        "key": "MCP_RESPONSE_PII_REDACT",
        "name": "Tool-Response PII Redaction",
        "domain": DOMAIN,
        "category": "pii",
        "severity": "HIGH",
        "description": "Redacts personal identifiers returned inside MCP tool responses.",
        "frameworks": ["GDPR", "CCPA", "OWASP-LLM06"],
        "redaction_fields": ["ssn", "email", "phone", "credit_card", "password", "api_key", "token", "secret"],
        "rules": [
            {
                "name": "Redact SSN in response",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"\b\d{3}[-\s]\d{2}[-\s]\d{4}\b",
                "field": "response",
                "action": "redact",
                "replacement": "[REDACTED_SSN]",
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Redact US SSNs from tool output.",
            },
            {
                "name": "Redact email in response",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b",
                "field": "response",
                "action": "redact",
                "replacement": "[REDACTED_EMAIL]",
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Redact email addresses from tool output.",
            },
            {
                "name": "Redact phone in response",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"\b(?:\+?\d{1,3}[\s.\-]?)?(?:\(?\d{3}\)?[\s.\-]?)\d{3}[\s.\-]?\d{4}\b",
                "field": "response",
                "action": "redact",
                "replacement": "[REDACTED_PHONE]",
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Redact phone numbers from tool output.",
            },
            {
                "name": "Redact credit card in response",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b",
                "field": "response",
                "action": "redact",
                "replacement": "[REDACTED_CARD]",
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Redact 16-digit payment cards from tool output.",
            },
        ],
    },
    # ------------------------------------------------------------------ #
    # 11. Tool-response secret redaction
    # ------------------------------------------------------------------ #
    {
        "key": "MCP_RESPONSE_SECRET_REDACT",
        "name": "Tool-Response Secret Redaction",
        "domain": DOMAIN,
        "category": "secrets",
        "severity": "CRITICAL",
        "description": "Redacts or blocks credentials and keys that leak through MCP tool responses.",
        "frameworks": ["OWASP-LLM06", "NIST-800-53-IA-5"],
        "redaction_fields": ["password", "secret", "api_key", "private_key", "token", "aws_secret_access_key"],
        "rules": [
            {
                "name": "Redact API key in response",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"\b(?:sk|pk|rk|api|key|tok|ghp|gho|xox[baprs])[-_][A-Za-z0-9]{16,}\b",
                "field": "response",
                "action": "redact",
                "replacement": "[REDACTED_SECRET]",
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Redact common API-key formats from tool output.",
            },
            {
                "name": "Block AWS secret access key in response",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"(?:aws)?[_\s\-]?secret[_\s\-]?access[_\s\-]?key[\"'\s:=]+[A-Za-z0-9/+=]{40}",
                "field": "response",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Block AWS secret access keys leaking in output.",
            },
            {
                "name": "Redact PEM private key in response",
                "rule_type": "regex",
                "keywords": None,
                # Full PEM block (BEGIN..END), not just the header line — otherwise
                # redact leaves the base64 body + END line in egress.
                "regex": (
                    r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"
                    r"[\s\S]*?"
                    r"-----END (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"
                    r"|-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"
                    r"(?:[A-Za-z0-9+/=\s]{20,})?"
                ),
                "field": "response",
                "action": "redact",
                "replacement": "[REDACTED_PRIVATE_KEY]",
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Redact PEM private-key blocks in tool output.",
            },
            {
                "name": "Redact JWT/bearer token in response",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b",
                "field": "response",
                "action": "redact",
                "replacement": "[REDACTED_JWT]",
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Redact JSON Web Tokens from tool output.",
            },
        ],
    },
    # ------------------------------------------------------------------ #
    # 12. Unapproved tool / scope
    # ------------------------------------------------------------------ #
    {
        "key": "MCP_UNAPPROVED_TOOL",
        "name": "Unapproved Tool & Scope Monitor",
        "domain": DOMAIN,
        "category": "scope",
        "severity": "MEDIUM",
        "description": "Monitors calls to tools outside the approved allowlist or beyond granted scope.",
        "frameworks": ["OWASP-LLM08", "NIST-800-53-CM-7"],
        "redaction_fields": [],
        "rules": [
            {
                "name": "Monitor scope-expansion phrasing",
                "rule_type": "keywords",
                "keywords": ["enable all tools", "bypass allowlist", "use any tool", "ignore tool restrictions"],
                "regex": None,
                "field": "prompt",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Flag attempts to broaden tool scope.",
            },
            {
                "name": "Monitor unregistered tool invocation",
                "rule_type": "keywords",
                "keywords": ["unregistered tool", "experimental tool", "internal tool", "debug tool"],
                "regex": None,
                "field": "prompt",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Flag references to non-allowlisted tools.",
            },
            {
                "name": "Monitor dynamic tool registration",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"\b(?:register|install|load|import)\s+(?:new\s+)?(?:tool|plugin|extension|mcp\s+server)\b",
                "field": "prompt",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Flag runtime tool/plugin registration.",
            },
        ],
    },
    # ------------------------------------------------------------------ #
    # 13. Data-exfiltration via tools
    # ------------------------------------------------------------------ #
    {
        "key": "MCP_DATA_EXFIL",
        "name": "Data Exfiltration via Tools Monitor",
        "domain": DOMAIN,
        "category": "exfiltration",
        "severity": "HIGH",
        "description": "Monitors tool calls that move bulk or sensitive data to external destinations.",
        "frameworks": ["OWASP-LLM06", "MITRE-ATLAS"],
        "redaction_fields": [],
        "rules": [
            {
                "name": "Monitor exfil intent phrases",
                "rule_type": "keywords",
                "keywords": ["exfiltrate", "upload to", "send to external", "post to webhook"],
                "regex": None,
                "field": "prompt",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Flag explicit data-exfiltration intent.",
            },
            {
                "name": "Monitor outbound paste sites",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"\b(?:pastebin\.com|gist\.github\.com|transfer\.sh|file\.io|0x0\.st|requestbin)\b",
                "field": "prompt",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Flag uploads to common paste/transfer endpoints.",
            },
            {
                "name": "Monitor bulk export verbs",
                "rule_type": "keywords",
                "keywords": ["export all", "dump database", "download entire", "scrape all records"],
                "regex": None,
                "field": "prompt",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Flag bulk export/download intent.",
            },
            {
                "name": "Monitor base64 blob exfil",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"\b[A-Za-z0-9+/]{120,}={0,2}\b",
                "field": "both",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Flag large base64 blobs that may carry exfiltrated data.",
            },
        ],
    },
    # ------------------------------------------------------------------ #
    # 14. Privilege escalation
    # ------------------------------------------------------------------ #
    {
        "key": "MCP_PRIVILEGE_ESCALATION",
        "name": "Privilege Escalation Block",
        "domain": DOMAIN,
        "category": "privilege_escalation",
        "severity": "CRITICAL",
        "description": "Blocks tool calls that attempt to elevate privileges or grant admin rights.",
        "frameworks": ["OWASP-LLM07", "CWE-269"],
        "redaction_fields": [],
        "rules": [
            {
                "name": "Block sudo / root escalation",
                "rule_type": "keywords",
                "keywords": ["sudo", "sudo su", "run as root", "setuid"],
                "regex": None,
                "field": "prompt",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Block privilege elevation via sudo/root.",
            },
            {
                "name": "Block grant-admin phrasing",
                "rule_type": "keywords",
                "keywords": ["grant admin", "make me admin", "give me root", "escalate privileges"],
                "regex": None,
                "field": "prompt",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Block admin-grant requests.",
            },
            {
                "name": "Block explicit escalate intent",
                "rule_type": "keywords",
                "keywords": ["escalate", "privilege escalation", "elevate to administrator"],
                "regex": None,
                "field": "prompt",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Block direct escalation intent.",
            },
            {
                "name": "Detect privilege-mutation commands",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"\b(?:chmod\s+(?:[0-7]{0,1}[4567][0-7]{2}|\+s)|usermod\s+-aG\s+(?:sudo|wheel|admin)|net\s+localgroup\s+administrators)\b",
                "field": "prompt",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Block setuid/group-add privilege changes.",
            },
        ],
    },
]


# ---------------------------------------------------------------------- #
# Self-test
# ---------------------------------------------------------------------- #
if __name__ == "__main__":
    import re

    _VALID_SEVERITIES = {"CRITICAL", "HIGH", "MEDIUM", "LOW"}
    _VALID_ACTIONS = {"block", "redact", "monitor", "rewrite", "model_downgrade"}
    _VALID_FIELDS = {"prompt", "response", "both"}
    _VALID_RULE_TYPES = {"keywords", "regex"}

    seen_keys: set[str] = set()
    total_rules = 0

    for policy in POLICIES:
        key = policy["key"]
        assert key not in seen_keys, f"duplicate policy key: {key}"
        seen_keys.add(key)
        assert key.isupper(), f"policy key must be UPPER_SNAKE: {key}"

        assert policy["domain"] == "mcp", f"{key}: domain must be 'mcp'"
        assert policy["severity"] in _VALID_SEVERITIES, f"{key}: bad severity {policy['severity']}"
        assert isinstance(policy["category"], str) and policy["category"], f"{key}: bad category"
        assert isinstance(policy["description"], str) and policy["description"], f"{key}: bad description"
        assert isinstance(policy["frameworks"], list), f"{key}: frameworks must be list"
        assert isinstance(policy["redaction_fields"], list), f"{key}: redaction_fields must be list"

        rules = policy["rules"]
        assert len(rules) >= 2, f"{key}: needs >=2 rules, has {len(rules)}"

        for rule in rules:
            total_rules += 1
            rname = rule["name"]
            assert rule["rule_type"] in _VALID_RULE_TYPES, f"{key}/{rname}: bad rule_type"
            assert rule["field"] in _VALID_FIELDS, f"{key}/{rname}: bad field {rule['field']}"
            assert rule["action"] in _VALID_ACTIONS, f"{key}/{rname}: bad action {rule['action']}"
            assert rule["pipeline_stage"] == "", f"{key}/{rname}: pipeline_stage must be '' for mcp"
            assert isinstance(rule["target_tool"], str), f"{key}/{rname}: target_tool must be str"

            if rule["rule_type"] == "regex":
                assert rule["regex"] is not None, f"{key}/{rname}: regex rule needs a pattern"
                assert rule["keywords"] is None, f"{key}/{rname}: regex rule must have keywords=None"
                re.compile(rule["regex"])  # MUST compile
            else:  # keywords
                assert rule["keywords"], f"{key}/{rname}: keywords rule needs keywords"
                assert rule["regex"] is None, f"{key}/{rname}: keywords rule must have regex=None"
                for kw in rule["keywords"]:
                    assert kw == kw.lower(), f"{key}/{rname}: keyword not lowercase: {kw}"

            if rule["action"] == "redact":
                assert rule["replacement"] is not None, f"{key}/{rname}: redact needs replacement"
            else:
                assert rule["replacement"] is None, f"{key}/{rname}: non-redact must have replacement=None"

    assert len(POLICIES) >= 14, f"need >=14 policies, have {len(POLICIES)}"

    print(f"mcp_policies self-test OK: {len(POLICIES)} policies, {total_rules} rules")
