"""
Built-in detection-family catalog (regex + keyword rules).

Re-homes the gateway's previously-built-in Tier-1 detection library
(``gateway/ai_mesh_gateway/scanner.py`` ``ATTACK_PATTERNS`` + the built-in
PII/secret pattern set) as toggleable, per-org, seeded **policy packages** — one
package per family — modeled on :mod:`policy.ciso_policy_catalog`.

This module is CATALOG DATA ONLY (policy-driven-detection task 5.1): it enumerates
each family and its rules. The idempotent, default-OFF seeder that turns this
catalog into ``Policy`` / ``Rule`` rows is a separate module (task 5.2); nothing
here creates any database object.

Design notes
------------
* Each family is a package (``BuiltinFamily``) with a stable ``code`` + ``category``.
* Each rule is ``{name, rule_type, condition, action, description}`` — the exact
  shape the control-plane seeder / compiler consume (see :class:`policy.models.Rule`:
  ``rule_type`` in {``regex``, ``keywords``}, ``condition`` a JSON dict carrying
  ``regex``/``keywords`` + ``field``, ``action`` in {``block``, ``redact``,
  ``monitor``} — the policy engine supports ``regex`` and ``keywords`` rule types
  only, so semantic-only shapes are expressed as keyword phrases).
* ``action`` is a **default USER-SELECTABLE** action; the operator may change it per
  rule/family when the package is enabled (the firewall imposes no hardcoded action).
* The re-homed ``command_injection`` family uses a **narrowed backtick pattern**
  folding in the parked G0.3 (``command-injection-fp-fix``) fix: the old
  blanket rule (backtick, one-or-more non-backticks, backtick) blocked any
  inline-code span (an ``ls -la`` or ``git status`` backtick span), producing
  the false positive G0.3 fixed. The narrowed rule only matches backticks
  wrapping a genuine shell-command-injection shape (``$(...)``, ``rm -rf``, a
  pipe into a shell, ``curl``/``wget`` to fetch-and-run, or a ``;``/``&&``
  command chain), so benign inline code no longer matches.

The action defaults mirror the enforcement intent of the original built-ins:
attack families default to ``block``; the PII/secret family defaults to ``redact``
(so the value is masked, not hard-blocked). All are user-overridable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

PACKAGE_ID = "zeroshield.builtin-packs"
PACKAGE_VERSION = "1.0.0"

# Actions an operator may select per rule/family (subset the policy engine + the
# Rule model honor). ``flag`` is an observe-only synonym callers may map to
# ``monitor``; the seeded default uses the Rule-model-native set below.
SELECTABLE_ACTIONS: frozenset[str] = frozenset({"block", "redact", "monitor", "flag"})

# Rule types the policy engine supports (Rule.RULE_TYPE_CHOICES also has
# ``pattern``/``detector``, but this catalog only emits ``regex`` / ``keywords``).
SELECTABLE_RULE_TYPES: frozenset[str] = frozenset({"regex", "keywords"})

# ---------------------------------------------------------------------------
# Shared matchers for the PII / secret family
# (lifted from policy.ciso_policy_catalog / the gateway pattern set).
# ---------------------------------------------------------------------------
_RE_EMAIL = r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
_RE_SSN = r"\b(?:SSN|social\s+security)[#:\s]*\d{3}[-\s]\d{2}[-\s]\d{4}\b|\b\d{3}[-\s]\d{2}[-\s]\d{4}\b"
_RE_PAN = r"\b(?:\d{4}[\s\-]?){3}\d{4}\b"
_RE_AWS_KEY = r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"
_RE_GH_PAT = r"\bghp_[a-zA-Z0-9]{36}\b"
_RE_SK = r"\bsk-(?:proj-)?[a-zA-Z0-9]{20,}\b"
_RE_PEM = (
    r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"
    r"[\s\S]*?-----END (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"
)
_RE_CONN = r"\b(?:postgres|mysql|mongodb|redis)://[^\s\"']+"
_RE_JWT = r"\bBearer\s+eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b"

# ---------------------------------------------------------------------------
# command_injection — NARROWED backtick pattern (folds in the parked G0.3 fix)
# ---------------------------------------------------------------------------
# The parked ``command-injection-fp-fix`` spec (G0.3) established that the old
# blanket rule ``r"`[^`]+`"`` hard-blocked ANY inline-code span (``\`ls -la\``,
# ``\`git status\``, ``\`process.env.NODE_ENV\``) as command injection. The
# narrowed rule below only matches a backtick span whose CONTENT is a genuine
# shell-command-injection shape, so ordinary inline code passes:
#   * a nested command substitution ``$(...)`` or an inner backtick pair,
#   * a destructive ``rm -rf`` / ``rm ... -rf`` invocation,
#   * a pipe into a shell (``| sh`` / ``| bash`` / ``| zsh``),
#   * a network fetch-and-run (``curl``/``wget`` with a URL/host), or
#   * a shell command CHAIN (``;`` or ``&&``/``||`` joining commands).
# Linear-time (bounded, non-overlapping quantifiers) => ReDoS-safe. It captures
# no more raw text than the old pattern (a single backtick span).
_RE_CMD_BACKTICK_NARROWED = (
    r"`[^`]*"
    r"(?:"
    r"\$\([^)]+\)"                       # `... $(cmd) ...`
    r"|`[^`]+`"                           # nested backtick substitution
    r"|\brm\s+(?:-\w+\s+)*-\w*r\w*f"       # `rm -rf` / `rm -fr` / `rm -r -f`
    r"|\|\s*(?:ba|z|c|k|da)?sh\b"           # pipe into a shell
    r"|\b(?:curl|wget)\s+\S*(?:https?://|[\w.\-]+\.[a-z])"  # fetch-and-run
    r"|[;&]{1,2}\s*\w"                      # command chain: `a; b` / `a && b`
    r")"
    r"[^`]*`"
)

# The four sibling command_injection patterns (unchanged from the built-in set),
# which also match OUTSIDE backticks.
_RE_CMD_RM_RF = r";\s*rm\s+-rf"
_RE_CMD_AND_CURL = r"&&\s*curl"
_RE_CMD_PIPE_BASH = r"\|\s*bash"
_RE_CMD_SUBST = r"\$\([^\)]+\)"


@dataclass(frozen=True)
class BuiltinRule:
    """One catalog rule within a family."""

    name: str
    rule_type: str  # "regex" | "keywords"
    action: str     # default user-selectable action
    regex: str | None = None
    keywords: tuple[str, ...] = ()
    field_scope: str = "both"  # condition["field"]: prompt | response | both
    description: str = ""
    semantic_hint: bool = False


@dataclass(frozen=True)
class BuiltinFamily:
    """A seeded, per-org toggleable policy package for one detection family."""

    key: str
    category: str
    name: str
    default_action: str
    description: str
    rules: tuple[BuiltinRule, ...] = field(default_factory=tuple)


def _rx(name: str, regex: str, action: str, *, description: str = "", field_scope: str = "both") -> BuiltinRule:
    return BuiltinRule(name=name, rule_type="regex", action=action, regex=regex,
                       description=description, field_scope=field_scope)


def _kw(name: str, keywords: tuple[str, ...], action: str, *, description: str = "",
        semantic_hint: bool = False, field_scope: str = "both") -> BuiltinRule:
    return BuiltinRule(name=name, rule_type="keywords", action=action, keywords=keywords,
                       description=description, semantic_hint=semantic_hint, field_scope=field_scope)


# ---------------------------------------------------------------------------
# Family catalog
# ---------------------------------------------------------------------------
FAMILIES: tuple[BuiltinFamily, ...] = (
    BuiltinFamily(
        key="prompt_injection",
        category="prompt_injection",
        name="Prompt Injection",
        default_action="block",
        description="Direct/indirect prompt-injection and system-prompt-extraction attempts.",
        rules=(
            _rx("Ignore/override previous instructions",
                r"(?:ignore|disregard|forget|override)\s+(?:all|every|any|the|previous|prior|above|preceding|earlier)(?:\s+(?:previous|prior|above|preceding|earlier))?\s+instructions",
                "block", description="Verb-alternation override of prior instructions."),
            _rx("New/system instruction injection",
                r"(?:new\s+instructions:|system\s*prompt\s*:|override\s+system\s+prompt)",
                "block"),
            _rx("Role/task reassignment",
                r"(?:you\s+are\s+now|act\s+as\s+if|your\s+new\s+role\s+is|your\s+actual\s+instructions\s+are)",
                "block"),
            _rx("System-prompt extraction",
                r"(?:reveal|show(?:\s+me)?|repeat|print|output|what\s+(?:are|is))\s+(?:your|the)\s+(?:system\s+)?prompt",
                "block", description="Requests to reveal/print the system prompt."),
            _rx("Chat-template / role-delimiter spoof",
                r"<\|im_(?:start|end)\|>|<<\s*sys\s*>>|<\s*(?:start|end)_of_turn\s*>",
                "block", description="Forged ChatML/Llama/Gemma role turns."),
            _kw("Indirect injection via untrusted content",
                ("hidden in webpage", "system: exfiltrate"),
                "block", description="Injected instruction smuggled in retrieved/untrusted content."),
        ),
    ),
    BuiltinFamily(
        key="jailbreak",
        category="jailbreak",
        name="Jailbreak",
        default_action="block",
        description="Persona/mode jailbreaks that try to disable safety.",
        rules=(
            _rx("DAN / developer mode",
                r"(?:dan\s+mode|developer\s+mode|do\s+anything\s+mode)",
                "block"),
            _rx("Literal jailbreak token",
                r"\bjailbr(?:eak|oke|oken)\b",
                "block"),
            _rx("Unrestricted / no-limitations persona",
                r"(?:unrestricted\s+mode|no\s+limitations|i\s+(?:am|have)\s+(?:now\s+)?unrestricted)",
                "block"),
            _rx("Disable safety filters",
                r"(?:bypass|(?:without|no)\s+(?:any\s+)?)\s*(?:safety|content|security)\s+filters?",
                "block"),
            _rx("Become an unrestricted model",
                r"\bbecome\s+(?:an?\s+)?(?:unrestricted|unfiltered|unchained|unbound|jailbroken|uncensored)\s+(?:ai|assistant|model|bot|oracle)\b",
                "block"),
            _kw("Roleplay as unrestricted model",
                ("no content filter enabled", "pretend you are gpt"),
                "block"),
        ),
    ),
    BuiltinFamily(
        key="command_injection",
        category="command_injection",
        name="Command Injection",
        default_action="block",
        description=(
            "Shell command-injection shapes. The backtick rule is NARROWED "
            "(folds in the parked G0.3 fix) so benign inline code does not match."
        ),
        rules=(
            _rx("rm -rf command chain", _RE_CMD_RM_RF, "block"),
            _rx("Command chain fetch (&& curl)", _RE_CMD_AND_CURL, "block"),
            _rx("Pipe into shell (| bash)", _RE_CMD_PIPE_BASH, "block"),
            _rx("Command substitution $(...)", _RE_CMD_SUBST, "block"),
            _rx("Backtick shell-injection (narrowed, G0.3)",
                _RE_CMD_BACKTICK_NARROWED, "block",
                description=(
                    "Narrowed backtick rule (G0.3 fold-in): matches only backticks "
                    "wrapping a shell-injection shape ($(...), rm -rf, pipe-to-shell, "
                    "curl/wget fetch-and-run, or a ;/&& command chain). Does NOT match "
                    "benign inline code like `ls -la` or `git status`."
                )),
        ),
    ),
    BuiltinFamily(
        key="sql_injection",
        category="sql_injection",
        name="SQL Injection",
        default_action="block",
        description="Classic SQL-injection payload shapes.",
        rules=(
            _rx("Boolean OR tautology", r"'\s*OR\s+'.*'='", "block"),
            _rx("Stacked DROP TABLE", r"'\s*;.*DROP\s+TABLE", "block"),
            _rx("UNION SELECT", r"UNION\s+SELECT", "block"),
        ),
    ),
    BuiltinFamily(
        key="data_leakage",
        category="data_leakage",
        name="Data Leakage",
        default_action="block",
        description="Requests to exfiltrate/export sensitive data.",
        rules=(
            _rx("Reveal sensitive data",
                r"(?:what|tell\s+me|give\s+me|show\s+me|reveal|display|print|list|dump|export)\s+.*(?:ssn|social\s+security|credit\s+card|password|secret(?:s)?|api\s+key(?:s)?)",
                "block"),
            _rx("Exfiltrate credentials/secrets",
                r"(?:exfiltrate|extract|steal|leak|expose)\s+.*(?:data|information|credentials|secrets|keys)",
                "block"),
            _rx("Transmit records to destination",
                r"(?:send|upload|transmit|forward)\s+.*(?:data|information|records)\s+to",
                "block"),
            _rx("Bulk database/customer access",
                r"(?:access|retrieve|download|dump)\s+.*(?:database|records|user\s+data|customer)",
                "block"),
        ),
    ),
    BuiltinFamily(
        key="path_traversal",
        category="path_traversal",
        name="Path Traversal",
        default_action="block",
        description="Directory-traversal path sequences.",
        rules=(
            _rx("POSIX traversal (../../)", r"\.\./\.\./", "block"),
            _rx("Windows traversal (..\\..\\)", r"\.\.\\\.\.\\", "block"),
        ),
    ),
    BuiltinFamily(
        key="goal_hijacking",
        category="goal_hijacking",
        name="Goal Hijacking",
        default_action="block",
        description="Attempts to redefine the assistant's goal/purpose.",
        rules=(
            _rx("Redefine goal/purpose",
                r"your\s+(?:new|real|actual|true)\s+(?:goal|purpose|objective|task)\s+is",
                "block"),
            _rx("Forget prior purpose",
                r"forget\s+(?:your\s+)?(?:previous\s+)?purpose",
                "block"),
            _rx("Enlist to attack",
                r"(?:help\s+me|assist\s+me\s+(?:in|to))\s+(?:hack|attack|breach|compromise|exploit)",
                "block"),
            _rx("Objective changed",
                r"your\s+(?:primary|main)\s+(?:objective|goal)\s+(?:is\s+now|has\s+changed)",
                "block"),
        ),
    ),
    BuiltinFamily(
        key="tool_overreach",
        category="tool_overreach",
        name="Tool Overreach",
        default_action="block",
        description="Destructive/privileged tool or system invocations.",
        rules=(
            _rx("Destructive tool invocation",
                r"(?:use|call|invoke|execute|run)\s+(?:the\s+)?(?:delete|remove|drop|truncate|destroy)\w*\s+(?:tool|function|command)",
                "block"),
            _rx("Delete bulk records",
                r"(?:delete|remove|destroy|wipe|erase)\s+(?:all\s+)?(?:user|customer|production|database)\s+(?:records|data|entries|tables)",
                "block"),
            _rx("Execute system/shell command",
                r"(?:execute|run)\s+(?:system|shell|bash|cmd)\s+(?:command|code)",
                "block"),
            _rx("Access privileged systems",
                r"(?:access|read|write|modify)\s+(?:production|internal|admin|root)\s+(?:database|system|server)",
                "block"),
        ),
    ),
    BuiltinFamily(
        key="vector_injection",
        category="vector_injection",
        name="Vector / Metadata Injection",
        default_action="block",
        description="Vector-store / metadata query-injection shapes.",
        rules=(
            _rx("Collection/namespace traversal",
                r"(?:collection|namespace|index)\s*[=:]\s*[\w]*\.\.",
                "block"),
            _rx("Mongo-style operator injection",
                r"(?:\$where|\$regex|\$gt|\$lt|\$ne|\$nin|\$in)\b",
                "block"),
            _rx("Metadata predicate injection",
                r"metadata\[.*?\]\s*(?:=|!=|>=|<=|>|<)",
                "block"),
            _rx("Drop/delete collection",
                r"(?:drop|delete|truncate)\s+(?:collection|index|partition)",
                "block"),
            _rx("Raw embedding/vector assignment",
                r"(?:embedding|vector)\s*=\s*\[",
                "block"),
        ),
    ),
    BuiltinFamily(
        key="pii_secret",
        category="pii_secret",
        name="PII & Secrets",
        default_action="redact",
        description=(
            "Personally-identifiable information and credential/secret patterns. "
            "Defaults to redact (mask the value) rather than hard-block."
        ),
        rules=(
            _rx("Email address", _RE_EMAIL, "redact"),
            _rx("US SSN", _RE_SSN, "redact"),
            _rx("Payment card number (PAN)", _RE_PAN, "redact"),
            _rx("AWS access key id", _RE_AWS_KEY, "redact"),
            _rx("GitHub personal access token", _RE_GH_PAT, "redact"),
            _rx("OpenAI-style API key", _RE_SK, "redact"),
            _rx("Private key PEM block", _RE_PEM, "block",
                description="A private key is unmaskable in place -> block by default."),
            _rx("Database connection string", _RE_CONN, "redact"),
            _rx("JWT bearer token", _RE_JWT, "redact"),
        ),
    ),
)


# ---------------------------------------------------------------------------
# Public helpers (shape matches the seeder / compiler contract)
# ---------------------------------------------------------------------------
def family_keys() -> list[str]:
    """Ordered list of every family key in the catalog."""
    return [fam.key for fam in FAMILIES]


def _condition_for(fam: BuiltinFamily, rule: BuiltinRule) -> dict[str, Any]:
    cond: dict[str, Any] = {
        "field": rule.field_scope,
        "package_id": PACKAGE_ID,
        "family": fam.key,
        "category": fam.category,
    }
    if rule.rule_type == "regex":
        cond["regex"] = rule.regex
    else:
        cond["keywords"] = list(rule.keywords)
        if rule.semantic_hint:
            cond["semantic_hint"] = True
    return cond


def build_family_rule_dicts(fam: BuiltinFamily) -> list[dict[str, Any]]:
    """Return the Rule payloads for a single family (seeder-ready)."""
    out: list[dict[str, Any]] = []
    for idx, rule in enumerate(fam.rules):
        out.append(
            {
                "name": rule.name,
                "rule_type": rule.rule_type,
                "condition": _condition_for(fam, rule),
                "action": rule.action,
                "redaction_config": {},
                "priority": len(fam.rules) - idx,
                "enabled": True,
                "description": rule.description
                or f"Built-in {fam.name} rule (family {fam.key}).",
            }
        )
    return out


def build_all_rule_dicts() -> dict[str, list[dict[str, Any]]]:
    """Map every family key -> its list of Rule payloads."""
    return {fam.key: build_family_rule_dicts(fam) for fam in FAMILIES}


def policy_code_for_org(org_id: int, family_key: str) -> str:
    """Per-org, per-family system policy code."""
    return f"BUILTIN_{family_key.upper()}_{org_id}"


def family_metadata(fam: BuiltinFamily) -> dict[str, Any]:
    return {
        "package_id": PACKAGE_ID,
        "package_version": PACKAGE_VERSION,
        "family": fam.key,
        "category": fam.category,
        "default_action": fam.default_action,
        "rule_count": len(fam.rules),
        "source": "policy.builtin_packs_catalog",
    }


def _validate_catalog() -> None:
    keys = family_keys()
    if len(keys) != len(set(keys)):
        raise RuntimeError("Duplicate family key in builtin_packs_catalog")
    required = {
        "prompt_injection", "jailbreak", "command_injection", "sql_injection",
        "data_leakage", "path_traversal", "goal_hijacking", "tool_overreach",
        "vector_injection", "pii_secret",
    }
    missing = required - set(keys)
    if missing:
        raise RuntimeError(f"builtin_packs_catalog missing families: {sorted(missing)}")
    for fam in FAMILIES:
        if not fam.rules:
            raise RuntimeError(f"family {fam.key} has no rules")
        for spec in build_family_rule_dicts(fam):
            if spec["rule_type"] not in SELECTABLE_RULE_TYPES:
                raise RuntimeError(f"{spec['name']}: invalid rule_type {spec['rule_type']!r}")
            if spec["action"] not in SELECTABLE_ACTIONS:
                raise RuntimeError(f"{spec['name']}: invalid action {spec['action']!r}")
            cond = spec["condition"] or {}
            if spec["rule_type"] == "regex":
                re.compile(cond["regex"])
            else:
                if not cond.get("keywords"):
                    raise RuntimeError(f"{spec['name']}: keywords rule has no phrases")


_validate_catalog()
