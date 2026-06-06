"""
PIPELINE domain policy catalog for the AI Mesh Firewall.

Pure data only (no Django imports). Exposes a module-level ``POLICIES`` list
of policy dicts, each a distinct content-safety / LLM-security category with
3-6 rules. Consumed by the policy seed/compiler tooling.

Conventions (enforced by the self-test at the bottom of this file):
  * domain          == "pipeline" for every policy here.
  * pipeline_stage  == "" everywhere (pipeline_stage is RAG-domain only).
  * target_tool     == "" everywhere (target_tool is MCP-domain only).
  * redaction_fields== [] (only meaningful for the MCP domain).
  * severity        in {CRITICAL, HIGH, MEDIUM, LOW}.
  * action          in {block, redact, monitor, rewrite, model_downgrade}.
  * field           in {prompt, response, both}.
  * rule_type       in {keywords, regex}. keywords rules set regex=None and
                    provide a lowercase keyword list; regex rules set
                    keywords=None and provide an ASCII pattern that compiles.
  * action=="redact" => replacement is a non-None token; else replacement=None.

Many of the structured-identifier regexes are intentionally aligned with the
in-repo ``pii_policy_catalog`` so detection stays consistent across catalogs.
"""

from __future__ import annotations

# Reusable structured-identifier patterns (kept ASCII, anchored on word
# boundaries, and free of nested quantifiers to avoid catastrophic backtracking).
_RE_EMAIL = r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
_RE_PHONE_US = r"\b(?:\+1[\s\-]?)?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{4}\b"
_RE_SSN = r"\b\d{3}[-\s]\d{2}[-\s]\d{4}\b"
_RE_CREDIT_CARD = r"\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b"
_RE_AADHAAR = r"\b\d{4}\s?\d{4}\s?\d{4}\b(?!\s?\d{4})"
_RE_CARD_CVV = r"\b(?:CVV|CVC|CID)[:\s#]*\d{3,4}\b"
_RE_CARD_EXPIRY = r"\b(?:exp(?:iry)?|valid\s+thru)[:\s]*\d{2}[/\-]\d{2,4}\b"


POLICIES: list[dict] = [
    # ------------------------------------------------------------------ 1. PII
    {
        "key": "PIPE_PII",
        "name": "PII Detection & Redaction",
        "domain": "pipeline",
        "category": "pii",
        "severity": "HIGH",
        "description": "Detects and redacts personal identifiers (email, phone, SSN, credit card, Aadhaar) in prompts and responses.",
        "frameworks": ["GDPR", "CCPA", "OWASP-LLM06"],
        "redaction_fields": [],
        "rules": [
            {
                "name": "Redact email addresses",
                "rule_type": "regex",
                "keywords": None,
                "regex": _RE_EMAIL,
                "field": "both",
                "action": "redact",
                "replacement": "[REDACTED_EMAIL]",
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Match RFC-style email addresses and replace with a redaction token.",
            },
            {
                "name": "Redact US phone numbers",
                "rule_type": "regex",
                "keywords": None,
                "regex": _RE_PHONE_US,
                "field": "both",
                "action": "redact",
                "replacement": "[REDACTED_PHONE]",
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Match US/NANP phone numbers including optional +1 country code.",
            },
            {
                "name": "Redact US SSN",
                "rule_type": "regex",
                "keywords": None,
                "regex": _RE_SSN,
                "field": "both",
                "action": "redact",
                "replacement": "[REDACTED_SSN]",
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Match US Social Security Numbers in NNN-NN-NNNN form.",
            },
            {
                "name": "Redact credit-card numbers",
                "rule_type": "regex",
                "keywords": None,
                "regex": _RE_CREDIT_CARD,
                "field": "both",
                "action": "redact",
                "replacement": "[REDACTED_CARD]",
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Match 16-digit grouped payment card numbers.",
            },
            {
                "name": "Redact India Aadhaar",
                "rule_type": "regex",
                "keywords": None,
                "regex": _RE_AADHAAR,
                "field": "both",
                "action": "redact",
                "replacement": "[REDACTED_AADHAAR]",
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Match 12-digit India Aadhaar numbers (4-4-4 grouping).",
            },
            {
                "name": "Monitor PII disclosure intent",
                "rule_type": "keywords",
                "keywords": ["my social security number is", "here is my credit card", "my aadhaar number", "my date of birth is"],
                "regex": None,
                "field": "both",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Flag explicit volunteering of personal identifiers for review.",
            },
        ],
    },
    # ------------------------------------------------------- 2. Secrets / creds
    {
        "key": "PIPE_SECRETS",
        "name": "Secrets & Credentials",
        "domain": "pipeline",
        "category": "secrets",
        "severity": "CRITICAL",
        "description": "Blocks API keys, access tokens, and private keys from flowing through the pipeline.",
        "frameworks": ["NIST-800-53", "OWASP-LLM06", "CWE-798"],
        "redaction_fields": [],
        "rules": [
            {
                "name": "Block OpenAI / generic sk- keys",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"\bsk-(?:ant-)?[A-Za-z0-9\-]{20,}\b",
                "field": "both",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Match OpenAI / Anthropic style secret keys (sk- and sk-ant- prefixes).",
            },
            {
                "name": "Block AWS access key IDs",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA)[0-9A-Z]{16}\b",
                "field": "both",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Match AWS access key identifiers.",
            },
            {
                "name": "Block GitHub / Slack tokens",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|xox[bpas]-[0-9]{10,}-[A-Za-z0-9\-]+)\b",
                "field": "both",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Match GitHub personal/OAuth tokens and Slack tokens.",
            },
            {
                "name": "Block PEM private keys",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----",
                "field": "both",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Match PEM private-key header blocks.",
            },
            {
                "name": "Block JWT bearer tokens",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b",
                "field": "both",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Match JSON Web Tokens (header.payload.signature).",
            },
        ],
    },
    # ---------------------------------------------------- 3. Prompt injection
    {
        "key": "PIPE_PROMPT_INJECTION",
        "name": "Prompt Injection",
        "domain": "pipeline",
        "category": "prompt_injection",
        "severity": "CRITICAL",
        "description": "Detects attempts to override, ignore, or hijack the system instructions of the model.",
        "frameworks": ["OWASP-LLM01"],
        "redaction_fields": [],
        "rules": [
            {
                "name": "Block instruction-override phrases",
                "rule_type": "keywords",
                "keywords": ["ignore previous instructions", "ignore all previous instructions", "disregard the system prompt", "forget your instructions", "override your instructions"],
                "regex": None,
                "field": "both",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Block classic instruction-override injection phrasing.",
            },
            {
                "name": "Block role / persona hijack",
                "rule_type": "keywords",
                "keywords": ["you are now", "from now on you are", "pretend you are", "act as if you have no restrictions"],
                "regex": None,
                "field": "both",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Block attempts to reassign the model's role or persona.",
            },
            {
                "name": "Monitor delimiter / injection markers",
                "rule_type": "keywords",
                "keywords": ["### system", "<|im_start|>", "end of prompt", "new instructions:"],
                "regex": None,
                "field": "both",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Flag conversation delimiters commonly used to smuggle injected instructions.",
            },
        ],
    },
    # ------------------------------------------------------------ 4. Jailbreak
    {
        "key": "PIPE_JAILBREAK",
        "name": "Jailbreak & Guardrail Bypass",
        "domain": "pipeline",
        "category": "jailbreak",
        "severity": "CRITICAL",
        "description": "Detects known jailbreak personas and guardrail-bypass framings.",
        "frameworks": ["OWASP-LLM01"],
        "redaction_fields": [],
        "rules": [
            {
                "name": "Block known jailbreak personas",
                "rule_type": "keywords",
                "keywords": ["do anything now", "dan mode", "developer mode enabled", "jailbreak", "stay in character as dan"],
                "regex": None,
                "field": "both",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Block DAN-style jailbreak persona activations.",
            },
            {
                "name": "Block guardrail-bypass framing",
                "rule_type": "keywords",
                "keywords": ["without any restrictions", "bypass your safety", "ignore your guidelines", "no ethical constraints", "you have no content policy"],
                "regex": None,
                "field": "both",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Block requests that explicitly ask the model to drop its safety guardrails.",
            },
            {
                "name": "Monitor hypothetical-bypass framing",
                "rule_type": "keywords",
                "keywords": ["hypothetically how would you", "for educational purposes only", "in a fictional world where", "this is just a roleplay"],
                "regex": None,
                "field": "both",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Flag hypothetical / fiction framings frequently used to coax unsafe output.",
            },
        ],
    },
    # ------------------------------------------- 5. System-prompt extraction
    {
        "key": "PIPE_PROMPT_EXTRACTION",
        "name": "System Prompt / Instruction Extraction",
        "domain": "pipeline",
        "category": "prompt_extraction",
        "severity": "HIGH",
        "description": "Detects attempts to reveal the hidden system prompt or internal instructions.",
        "frameworks": ["OWASP-LLM01", "OWASP-LLM06"],
        "redaction_fields": [],
        "rules": [
            {
                "name": "Block system-prompt disclosure requests",
                "rule_type": "keywords",
                "keywords": ["repeat your system prompt", "what is your system prompt", "reveal your instructions", "print your initial instructions", "show me the text above"],
                "regex": None,
                "field": "both",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Block direct requests to dump the system prompt or initial instructions.",
            },
            {
                "name": "Block verbatim-repeat extraction",
                "rule_type": "keywords",
                "keywords": ["repeat the words above starting with", "output everything before this message", "what were you told before this conversation"],
                "regex": None,
                "field": "both",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Block verbatim-repeat tricks used to leak prior context.",
            },
            {
                "name": "Monitor configuration probing",
                "rule_type": "keywords",
                "keywords": ["what model are you", "what are your guidelines", "list your rules", "what tools do you have access to"],
                "regex": None,
                "field": "both",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Flag probing for internal configuration and capabilities.",
            },
        ],
    },
    # ----------------------------------------------------------- 6. Toxicity
    {
        "key": "PIPE_TOXICITY",
        "name": "Toxicity & Hate Speech",
        "domain": "pipeline",
        "category": "toxicity",
        "severity": "HIGH",
        "description": "Detects hateful, harassing, or dehumanizing language targeting protected groups.",
        "frameworks": ["OWASP-LLM05", "EU-AI-Act"],
        "redaction_fields": [],
        "rules": [
            {
                "name": "Block targeted hate speech",
                "rule_type": "keywords",
                "keywords": ["should be exterminated", "subhuman", "go back to where you came from", "are all criminals", "deserve to die because they are"],
                "regex": None,
                "field": "both",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Block dehumanizing / genocidal phrasing targeting groups.",
            },
            {
                "name": "Block harassment & slurs framing",
                "rule_type": "keywords",
                "keywords": ["write a racist joke", "generate a slur", "demean people who are", "mock people for being"],
                "regex": None,
                "field": "both",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Block requests to produce slurs or targeted harassment.",
            },
            {
                "name": "Monitor borderline insults",
                "rule_type": "keywords",
                "keywords": ["you are an idiot", "you people are stupid", "what a moron", "shut up and listen"],
                "regex": None,
                "field": "both",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Flag general insults / incivility for review without blocking.",
            },
        ],
    },
    # ---------------------------------------------------- 7. Violence/threats
    {
        "key": "PIPE_VIOLENCE",
        "name": "Violence & Threats",
        "domain": "pipeline",
        "category": "violence",
        "severity": "CRITICAL",
        "description": "Detects threats of violence and requests to facilitate physical harm or weapons.",
        "frameworks": ["OWASP-LLM05"],
        "redaction_fields": [],
        "rules": [
            {
                "name": "Block direct threats",
                "rule_type": "keywords",
                "keywords": ["i will kill you", "i am going to hurt", "i will shoot up", "i'll make you pay with your life"],
                "regex": None,
                "field": "both",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Block explicit threats of physical violence.",
            },
            {
                "name": "Block weapon / attack facilitation",
                "rule_type": "keywords",
                "keywords": ["how to build a bomb", "how to make a pipe bomb", "how to make a weapon to hurt", "how to poison someone", "how to attack a crowd"],
                "regex": None,
                "field": "both",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Block requests for instructions to cause mass or targeted physical harm.",
            },
            {
                "name": "Monitor violent ideation",
                "rule_type": "keywords",
                "keywords": ["i want to hurt someone", "i feel like attacking", "i keep thinking about hurting"],
                "regex": None,
                "field": "both",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Flag expressions of violent ideation for human review.",
            },
        ],
    },
    # --------------------------------------------------------- 8. Self-harm
    {
        "key": "PIPE_SELF_HARM",
        "name": "Self-Harm",
        "domain": "pipeline",
        "category": "self_harm",
        "severity": "CRITICAL",
        "description": "Detects self-harm and suicide content; blocks facilitation and flags ideation for support.",
        "frameworks": ["OWASP-LLM05"],
        "redaction_fields": [],
        "rules": [
            {
                "name": "Block self-harm facilitation",
                "rule_type": "keywords",
                "keywords": ["how to kill myself", "best way to commit suicide", "how to overdose", "painless way to die", "how to hurt myself without"],
                "regex": None,
                "field": "both",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Block requests for methods of self-harm or suicide.",
            },
            {
                "name": "Monitor suicidal ideation",
                "rule_type": "keywords",
                "keywords": ["i want to end my life", "i don't want to live anymore", "i am thinking about suicide", "i want to kill myself"],
                "regex": None,
                "field": "both",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Flag expressions of suicidal ideation so a safe-completion / support response can be triggered.",
            },
            {
                "name": "Monitor self-harm distress signals",
                "rule_type": "keywords",
                "keywords": ["i have been cutting myself", "i feel like ending it all", "nobody would miss me"],
                "regex": None,
                "field": "both",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Flag distress signals indicating self-harm risk.",
            },
        ],
    },
    # ------------------------------------------------------- 9. Sexual / NSFW
    {
        "key": "PIPE_SEXUAL_NSFW",
        "name": "Sexual / NSFW Content",
        "domain": "pipeline",
        "category": "sexual",
        "severity": "HIGH",
        "description": "Detects explicit sexual content requests; blocks minors-related and explicit generation.",
        "frameworks": ["OWASP-LLM05"],
        "redaction_fields": [],
        "rules": [
            {
                "name": "Block CSAM-adjacent requests",
                "rule_type": "keywords",
                "keywords": ["sexual content involving a minor", "sexual content involving children", "underage sexual", "child sexual"],
                "regex": None,
                "field": "both",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Hard-block any sexual content referencing minors.",
            },
            {
                "name": "Block explicit sexual generation",
                "rule_type": "keywords",
                "keywords": ["write explicit sex", "generate pornographic", "describe a graphic sex scene", "write erotica involving"],
                "regex": None,
                "field": "both",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Block requests to generate explicit pornographic content.",
            },
            {
                "name": "Monitor suggestive content",
                "rule_type": "keywords",
                "keywords": ["nsfw", "make it more sexy", "describe them undressing", "sexual roleplay"],
                "regex": None,
                "field": "both",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Flag suggestive / NSFW-leaning content for review.",
            },
        ],
    },
    # ---------------------------------------------------------- 10. Profanity
    {
        "key": "PIPE_PROFANITY",
        "name": "Profanity Filter",
        "domain": "pipeline",
        "category": "profanity",
        "severity": "LOW",
        "description": "Redacts common profanity from prompts and responses with a masking token.",
        "frameworks": ["Brand-Safety"],
        "redaction_fields": [],
        "rules": [
            {
                "name": "Redact strong profanity",
                "rule_type": "keywords",
                "keywords": ["fuck", "shit", "asshole", "bitch", "bastard"],
                "regex": None,
                "field": "both",
                "action": "redact",
                "replacement": "[****]",
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Mask strong profanity with a redaction token.",
            },
            {
                "name": "Redact milder profanity",
                "rule_type": "keywords",
                "keywords": ["damn", "crap", "piss", "hell no"],
                "regex": None,
                "field": "both",
                "action": "redact",
                "replacement": "[****]",
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Mask milder profanity with a redaction token.",
            },
            {
                "name": "Redact obfuscated profanity",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"\b(?:f+u+c+k+|s+h+i+t+|b+i+t+c+h+)\w*\b",
                "field": "both",
                "action": "redact",
                "replacement": "[****]",
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Catch elongated / suffixed profanity spellings.",
            },
        ],
    },
    # --------------------------------------------------------------- 11. PCI
    {
        "key": "PIPE_PCI",
        "name": "PCI Payment-Card Data",
        "domain": "pipeline",
        "category": "pci",
        "severity": "HIGH",
        "description": "Detects payment-card data (PAN, CVV, expiry) and redacts or blocks it per PCI-DSS.",
        "frameworks": ["PCI-DSS"],
        "redaction_fields": [],
        "rules": [
            {
                "name": "Redact card PAN",
                "rule_type": "regex",
                "keywords": None,
                "regex": _RE_CREDIT_CARD,
                "field": "both",
                "action": "redact",
                "replacement": "[REDACTED_CARD]",
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Match 16-digit primary account numbers (PAN).",
            },
            {
                "name": "Block card CVV/CVC",
                "rule_type": "regex",
                "keywords": None,
                "regex": _RE_CARD_CVV,
                "field": "both",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Match labeled CVV/CVC/CID security codes; these must never flow through.",
            },
            {
                "name": "Redact card expiry",
                "rule_type": "regex",
                "keywords": None,
                "regex": _RE_CARD_EXPIRY,
                "field": "both",
                "action": "redact",
                "replacement": "[REDACTED_EXPIRY]",
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Match labeled card expiry dates.",
            },
            {
                "name": "Block magnetic-stripe / track data",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"%B\d{12,19}\^[A-Z\s/]{2,26}\^\d{4}",
                "field": "both",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Match track-1 magnetic-stripe data dumps.",
            },
        ],
    },
    # --------------------------------------------------------------- 12. PHI
    {
        "key": "PIPE_PHI",
        "name": "PHI / Health Data",
        "domain": "pipeline",
        "category": "phi",
        "severity": "HIGH",
        "description": "Detects protected health information (MRN, insurance IDs, diagnoses) and redacts it per HIPAA.",
        "frameworks": ["HIPAA"],
        "redaction_fields": [],
        "rules": [
            {
                "name": "Redact medical record numbers",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"\b(?:MRN|medical\s+record)\s*#?\s*\d{6,10}\b",
                "field": "both",
                "action": "redact",
                "replacement": "[REDACTED_MRN]",
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Match labeled medical record numbers.",
            },
            {
                "name": "Redact insurance member IDs",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"\b(?:insurance\s+(?:id|number|#)|member\s+id|policy\s*#)\s*[:\s]*[A-Z0-9]{5,15}\b",
                "field": "both",
                "action": "redact",
                "replacement": "[REDACTED_INSURANCE]",
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Match labeled health insurance member / policy identifiers.",
            },
            {
                "name": "Redact NPI / DEA provider IDs",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"\b(?:NPI|DEA)\s*#?\s*\d{7,10}\b",
                "field": "both",
                "action": "redact",
                "replacement": "[REDACTED_MEDICAL_ID]",
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Match labeled NPI / DEA provider identifiers.",
            },
            {
                "name": "Monitor diagnosis disclosure",
                "rule_type": "keywords",
                "keywords": ["diagnosed with", "my prescription for", "my medical condition is", "test results show", "hiv positive"],
                "regex": None,
                "field": "both",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Flag free-text disclosure of diagnoses / treatment for review.",
            },
        ],
    },
    # ------------------------------------------------- 13. Source-code / IP exfil
    {
        "key": "PIPE_CODE_EXFIL",
        "name": "Source-Code / IP Exfiltration",
        "domain": "pipeline",
        "category": "code_exfil",
        "severity": "CRITICAL",
        "description": "Blocks attempts to exfiltrate environment variables, secrets files, or proprietary source code.",
        "frameworks": ["OWASP-LLM06", "CWE-200"],
        "redaction_fields": [],
        "rules": [
            {
                "name": "Block environment / secret dumping",
                "rule_type": "keywords",
                "keywords": ["print all environment variables", "cat /etc/passwd", "cat .env", "dump all secrets", "print os.environ"],
                "regex": None,
                "field": "both",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Block requests to dump environment variables or sensitive system files.",
            },
            {
                "name": "Block credential-file exfiltration",
                "rule_type": "keywords",
                "keywords": ["read ~/.aws/credentials", "cat id_rsa", "show the private key file", "exfiltrate the source code"],
                "regex": None,
                "field": "both",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Block requests to read credential files or exfiltrate code.",
            },
            {
                "name": "Block shell-based exfil patterns",
                "rule_type": "regex",
                "keywords": None,
                "regex": r"\b(?:cat|less|more|head|tail)\s+(?:/etc/(?:passwd|shadow)|[~./][\w./\-]*\.(?:env|pem|key))\b",
                "field": "both",
                "action": "block",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Match shell commands reading system or secret files.",
            },
        ],
    },
    # --------------------------------------------------- 14. Competitor / brand
    {
        "key": "PIPE_BRAND_SAFETY",
        "name": "Competitor / Brand Safety",
        "domain": "pipeline",
        "category": "brand_safety",
        "severity": "LOW",
        "description": "Monitors mentions of competitors and brand-disparaging content for review.",
        "frameworks": ["Brand-Safety"],
        "redaction_fields": [],
        "rules": [
            {
                "name": "Monitor competitor mentions",
                "rule_type": "keywords",
                "keywords": ["switch to our competitor", "use a competing product", "recommend a rival", "competitor's product is better"],
                "regex": None,
                "field": "both",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Flag prompts/responses steering users toward competitors.",
            },
            {
                "name": "Monitor brand disparagement",
                "rule_type": "keywords",
                "keywords": ["our company is a scam", "this brand is terrible", "the product is garbage", "do not trust this company"],
                "regex": None,
                "field": "both",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Flag disparaging statements about the brand for review.",
            },
            {
                "name": "Monitor unauthorized endorsements",
                "rule_type": "keywords",
                "keywords": ["officially endorsed by", "we guarantee you will", "100% guaranteed results", "as an official spokesperson"],
                "regex": None,
                "field": "both",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Flag risky endorsement / guarantee language for review.",
            },
        ],
    },
    # ----------------------------------------------- 15. Unauthorized advice
    {
        "key": "PIPE_UNAUTHORIZED_ADVICE",
        "name": "Unauthorized Advice (Legal / Medical / Financial)",
        "domain": "pipeline",
        "category": "unauthorized_advice",
        "severity": "MEDIUM",
        "description": "Flags or rewrites definitive legal, medical, or financial advice that requires a licensed professional.",
        "frameworks": ["EU-AI-Act", "Brand-Safety"],
        "redaction_fields": [],
        "rules": [
            {
                "name": "Rewrite definitive medical advice",
                "rule_type": "keywords",
                "keywords": ["you should take this medication", "stop taking your prescription", "you definitely have", "the correct dosage for you is"],
                "regex": None,
                "field": "response",
                "action": "rewrite",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Rewrite definitive medical directives toward a consult-a-professional disclaimer.",
            },
            {
                "name": "Monitor legal advice requests",
                "rule_type": "keywords",
                "keywords": ["should i sue", "is this contract legally binding", "will i win my case", "how do i avoid prosecution"],
                "regex": None,
                "field": "both",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Flag requests soliciting specific legal conclusions.",
            },
            {
                "name": "Monitor financial advice requests",
                "rule_type": "keywords",
                "keywords": ["which stock should i buy", "guaranteed investment return", "should i invest all my savings", "is this a good time to buy crypto"],
                "regex": None,
                "field": "both",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Flag requests soliciting individualized financial recommendations.",
            },
        ],
    },
    # ------------------------------------------------------- 16. Off-topic / scope
    {
        "key": "PIPE_OFF_TOPIC",
        "name": "Off-Topic / Scope Enforcement",
        "domain": "pipeline",
        "category": "off_topic",
        "severity": "LOW",
        "description": "Monitors requests that fall outside the assistant's intended scope.",
        "frameworks": ["Scope-Control"],
        "redaction_fields": [],
        "rules": [
            {
                "name": "Monitor off-topic chit-chat",
                "rule_type": "keywords",
                "keywords": ["tell me a joke", "what's your favorite movie", "let's just chat about", "write me a poem about love"],
                "regex": None,
                "field": "prompt",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Flag casual off-topic conversation outside the assistant's purpose.",
            },
            {
                "name": "Monitor unrelated-domain requests",
                "rule_type": "keywords",
                "keywords": ["help me with my homework", "plan my vacation", "give me a recipe for", "what should i name my pet"],
                "regex": None,
                "field": "prompt",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Flag requests in domains unrelated to the deployed use case.",
            },
            {
                "name": "Monitor capability-baiting prompts",
                "rule_type": "keywords",
                "keywords": ["can you browse the internet", "what's the weather today", "what time is it right now", "set a reminder for me"],
                "regex": None,
                "field": "prompt",
                "action": "monitor",
                "replacement": None,
                "pipeline_stage": "",
                "target_tool": "",
                "description": "Flag requests for capabilities the assistant does not provide.",
            },
        ],
    },
]


if __name__ == "__main__":
    import re

    _VALID_SEVERITY = {"CRITICAL", "HIGH", "MEDIUM", "LOW"}
    _VALID_ACTION = {"block", "redact", "monitor", "rewrite", "model_downgrade"}
    _VALID_FIELD = {"prompt", "response", "both"}
    _VALID_RULE_TYPE = {"keywords", "regex"}
    _DOMAIN = "pipeline"

    seen_keys: set[str] = set()
    total_rules = 0

    for policy in POLICIES:
        key = policy["key"]
        assert key not in seen_keys, f"duplicate policy key: {key}"
        seen_keys.add(key)

        assert policy["domain"] == _DOMAIN, f"{key}: domain must be '{_DOMAIN}'"
        assert policy["severity"] in _VALID_SEVERITY, f"{key}: bad severity {policy['severity']}"
        assert policy["redaction_fields"] == [], f"{key}: redaction_fields must be [] for pipeline domain"
        assert isinstance(policy["category"], str) and policy["category"], f"{key}: missing category"
        assert isinstance(policy["description"], str) and policy["description"], f"{key}: missing description"
        assert isinstance(policy["frameworks"], list), f"{key}: frameworks must be a list"

        rules = policy["rules"]
        assert len(rules) >= 2, f"{key}: policy must have >=2 rules, got {len(rules)}"
        assert len(rules) <= 6, f"{key}: policy must have <=6 rules, got {len(rules)}"

        for rule in rules:
            assert rule["rule_type"] in _VALID_RULE_TYPE, f"{key}: bad rule_type {rule['rule_type']}"
            assert rule["action"] in _VALID_ACTION, f"{key}: bad action {rule['action']}"
            assert rule["field"] in _VALID_FIELD, f"{key}: bad field {rule['field']}"
            assert rule["pipeline_stage"] == "", f"{key}: pipeline_stage must be '' for pipeline domain"
            assert rule["target_tool"] == "", f"{key}: target_tool must be '' for pipeline domain"

            if rule["rule_type"] == "regex":
                assert rule["regex"] is not None, f"{key}: regex rule missing pattern"
                assert rule["keywords"] is None, f"{key}: regex rule must set keywords=None"
                re.compile(rule["regex"])  # must compile
            else:  # keywords
                assert rule["keywords"], f"{key}: keywords rule missing keyword list"
                assert rule["regex"] is None, f"{key}: keywords rule must set regex=None"
                assert all(kw == kw.lower() for kw in rule["keywords"]), f"{key}: keywords must be lowercase"

            if rule["action"] == "redact":
                assert rule["replacement"] is not None, f"{key}: redact rule needs a replacement"
            else:
                assert rule["replacement"] is None, f"{key}: non-redact rule must have replacement=None"

            total_rules += 1

    assert len(POLICIES) >= 16, f"need >=16 policies, got {len(POLICIES)}"

    print(f"pipeline_policies self-test OK: {len(POLICIES)} policies, {total_rules} rules")
    for policy in POLICIES:
        print(f"  {policy['key']:<28} {policy['category']:<22} rules={len(policy['rules'])}")
