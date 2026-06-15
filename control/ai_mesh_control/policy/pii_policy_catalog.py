"""
Comprehensive PII / PHI / PCI / secrets policy rule catalog (50+ matchers).

Patterns align with Microsoft Presidio entity families, NIST SP 800-122 PII
categories, GDPR Art. 4 identifiers, and the in-repo ``pii_detector`` engine.
Used by ``seed_pii_policy_package`` to attach one org-scoped policy with all rules.
"""

from __future__ import annotations

import re
from typing import Any

PACKAGE_ID = "pii_comprehensive_v1"
PACKAGE_VERSION = "1.0.0"
MIN_RULE_COUNT = 50

# (entity_key, display_name, regex, replacement, action, frameworks)
# action: redact for PII; block for credential material that must not flow through LLMs
_RULE_SPECS: list[tuple[str, str, str, str, str, tuple[str, ...]]] = [
    # --- Contact & identity (GDPR / CCPA) ---
    ("EMAIL_ADDRESS", "Email address", r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b", "[REDACTED_EMAIL]", "redact", ("GDPR", "CCPA")),
    ("EMAIL_PLUS_ALIAS", "Email with plus-tag", r"\b[A-Za-z0-9._%+\-]+\+[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b", "[REDACTED_EMAIL]", "redact", ("GDPR",)),
    ("PHONE_US", "US phone number", r"\b(?:\+?\d{1,3}[\s.\-]*)?(?:\(\d{3}\)|\d{3})[\s.\-]+\d{3}[\s.\-]+\d{4}\b", "[REDACTED_PHONE]", "redact", ("CCPA",)),
    # PHONE_INTERNATIONAL: the leading \b sits between the preceding char and the
    # '+'. When the number is preceded by a space or word ("call +447911123456"),
    # space/'+' are both non-word, so \b finds no boundary and a contiguous E.164
    # number is silently missed (a recall gap). Replace the leading \b with a
    # negative lookbehind that only rejects a '+' glued to a word char or another
    # '+', so the number is caught regardless of the preceding separator.
    ("PHONE_INTERNATIONAL", "International phone (E.164-style)", r"(?<![\w+])\+[1-9]\d{7,14}\b", "[REDACTED_PHONE]", "redact", ("GDPR",)),
    ("PHONE_IN", "India mobile number", r"\+91[\s\-]?[6-9]\d{9}\b|\b[6-9]\d{4}[\s\-]\d{5}\b", "[REDACTED_PHONE]", "redact", ("GDPR",)),
    ("PHONE_UK", "UK phone number", r"\b(?:\+44|0)\s?(?:\d\s?){9,10}\b", "[REDACTED_PHONE]", "redact", ("GDPR",)),
    ("PHONE_GENERIC", "Generic phone (10+ digits)", r"\b(?:\+\d{1,3}[\s.\-]+)?\(?\d{2,4}\)?[\s.\-]+\d{3,4}[\s.\-]+\d{3,4}\b", "[REDACTED_PHONE]", "redact", ("GDPR", "CCPA")),
    ("PERSON_WITH_TITLE", "Person name with honorific", r"\b(?:Mr\.|Mrs\.|Ms\.|Miss|Dr\.|Prof\.)\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3}\b", "[REDACTED_NAME]", "redact", ("GDPR",)),
    # DATE_OF_BIRTH: the old anchor required the literal "born on", missing the
    # common bare "born <date>" phrasing (a recall gap). Make the "on" optional.
    ("DATE_OF_BIRTH", "Date of birth (labeled)", r"\b(?:DOB|D\.O\.B\.|date\s+of\s+birth|born(?:\s+on)?)[:\s=]*\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}\b", "[REDACTED_DOB]", "redact", ("GDPR", "HIPAA")),
    ("STREET_ADDRESS", "US-style street address", r"\b\d{1,5}\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s+(?:Street|St|Avenue|Ave|Boulevard|Blvd|Drive|Dr|Road|Rd|Lane|Ln|Way|Court|Ct)\.?\b", "[REDACTED_ADDRESS]", "redact", ("GDPR", "CCPA")),
    ("US_ZIP", "US ZIP code", r"\b\d{5}-\d{4}\b|\b(?:zip|postal)(?:\s*code)?[:#\s]+\d{5}(?:-\d{4})?\b|\b[A-Z]{2}\s+\d{5}(?:-\d{4})?\b", "[REDACTED_ZIP]", "redact", ("CCPA",)),
    ("GEO_COORDINATES", "Latitude/longitude pair", r"\b-?\d{1,3}\.\d{4,},\s*-?\d{1,3}\.\d{4,}\b", "[REDACTED_GEO]", "redact", ("GDPR",)),
    # --- US government IDs ---
    ("US_SSN", "US Social Security Number", r"\b\d{3}[-\s]\d{2}[-\s]\d{4}\b", "[REDACTED_SSN]", "redact", ("CCPA", "NIST")),
    ("US_SSN_LABELLED", "US SSN (labeled)", r"\b(?:SSN|social\s+security)[#:\s]*\d{3}[-\s]?\d{2}[-\s]?\d{4}\b", "[REDACTED_SSN]", "redact", ("CCPA", "NIST")),
    # US_PASSPORT / US_DRIVER_LICENSE: the labeled body [A-Z0-9]{n} compiles under
    # IGNORECASE, so the alnum run greedily absorbs the next plain-English word
    # after the label ("passport renewal" -> body "renewal"). Require the body to
    # contain >=1 digit via a lookahead, so an all-letter prose word can't pose as
    # an ID. The (?=[A-Z0-9]{n}\b) preserves the original length bound.
    ("US_PASSPORT", "US passport number (labeled)", r"\b(?:passport)[#:\s]*(?=[A-Z0-9]{6,9}\b)[A-Z0-9]*\d[A-Z0-9]*\b", "[REDACTED_PASSPORT]", "redact", ("NIST",)),
    ("US_DRIVER_LICENSE", "US driver license (labeled)", r"\b(?:DL|driver'?s?\s*(?:license|lic))[#:\s]*(?=[A-Z0-9]{5,12}\b)[A-Z0-9]*\d[A-Z0-9]*\b", "[REDACTED_DL]", "redact", ("CCPA",)),
    # US_EIN: the bare NN-NNNNNNN shape matches any invoice/part/order number with
    # the same digit grouping. Require an EIN / employer-id / federal-tax-id label
    # so only the labeled identifier redacts.
    ("US_EIN", "US Employer Identification Number", r"\b(?:EIN|employer\s+id(?:entification)?(?:\s*(?:no|number))?|federal\s+tax\s+id(?:entification)?(?:\s*(?:no|number))?)[:#\s]*\d{2}-\d{7}\b", "[REDACTED_EIN]", "redact", ("NIST",)),
    # MEDICARE_MBI: the old \b[1-9][A-HJ-NP-Z0-9]{10}\b matched any 11-char alnum
    # token starting non-zero — including a bare 11-digit number and 11-char
    # hashes. Use the real CMS positional MBI layout: C A AN N A AN N A A N N
    # (C=1-9; A=alpha excl S,L,O,I,B,Z; AN=alnum; N=digit), which forces a mix of
    # letters AND digits in fixed positions, plus an MBI/Medicare label branch
    # (with optional 4-3-4 hyphen/space grouping) for the labeled form.
    ("MEDICARE_MBI", "Medicare Beneficiary Identifier", r"\b(?:MBI|medicare(?:\s*(?:beneficiary)?\s*(?:id|identifier|number|no|#)?)?)[:#\s]+[1-9][AC-HJ-NP-RT-Y][AC-HJ-NP-RT-Y0-9]\d[\-\s]?[AC-HJ-NP-RT-Y][AC-HJ-NP-RT-Y0-9]\d[\-\s]?[AC-HJ-NP-RT-Y]{2}\d{2}\b|\b[1-9][AC-HJ-NP-RT-Y][AC-HJ-NP-RT-Y0-9]\d[AC-HJ-NP-RT-Y][AC-HJ-NP-RT-Y0-9]\d[AC-HJ-NP-RT-Y]{2}\d{2}\b", "[REDACTED_MBI]", "redact", ("HIPAA",)),
    # --- UK / EU ---
    # UK_NINO: real NINOs are printed both contiguously ("AB123456C") and with
    # space/hyphen separators between the prefix/number/suffix groups
    # ("AB 12 34 56 C"). The old \b...\d{6}...\b only matched the contiguous form,
    # silently missing every spaced/hyphenated NINO (a recall gap). Allow optional
    # single space/hyphen separators between the 2-2-2 digit groups and suffix.
    ("UK_NINO", "UK National Insurance Number", r"\b[A-CEGHJ-PR-TW-Z]{2}[\s\-]?\d{2}[\s\-]?\d{2}[\s\-]?\d{2}[\s\-]?[A-D]\b", "[REDACTED_NINO]", "redact", ("GDPR",)),
    ("UK_NHS", "UK NHS number", r"\b\d{3}\s\d{3}\s\d{4}\b", "[REDACTED_NHS]", "redact", ("GDPR", "HIPAA")),
    # EU_VAT: the old \b[A-Z]{2}\d{8,12}\b matched ANY two-letter + 8-12-digit
    # token under IGNORECASE — invoice/order refs ("GB12345678"), and lowercase
    # prose ("xx12345678"). Tighten to one of two prose-free signals: (A) an
    # explicit VAT label before the number (any 2-letter prefix), or (B) the real
    # per-country VAT body, where each ISO-3166 country code is followed by that
    # country's EXACT VAT structure (e.g. DE = 9 digits, IT/LV/HR = 11, NL =
    # 9 digits + "B" + 2, ES = char/digit + 7 digits + char). A generic two-letter
    # token ("XX"/"AB") and a wrong-length body for a real code ("GB12345678",
    # which is 8 not the GB-branch length) no longer qualify under branch B; only a
    # labeled number (branch A) covers prefixes outside the per-country set. The
    # digit runs use [0-9] (not \d) because the write-time ReDoS heuristic strips
    # backslash escapes before scanning, and a \d{8,12} directly after the country
    # alternation would, post-strip, look like a quantified alternation group. The
    # outer alternation group carries no trailing quantifier, so the per-country
    # alternation is not flagged as a quantified-alternation ReDoS shape.
    ("EU_VAT", "EU VAT number", r"\b(?:VAT(?:\s*(?:no|number|id|reg(?:\.|istration)?)?)?[:#\s]+[A-Z]{2}[0-9]{8,12}|AT[Uu][0-9]{8}|BE[0-9]{10}|BG[0-9]{9,10}|HR[0-9]{11}|CY[0-9]{8}[A-Z]|CZ[0-9]{8,10}|DK[0-9]{8}|EE[0-9]{9}|FI[0-9]{8}|FR[0-9A-Z]{2}[0-9]{9}|DE[0-9]{9}|EL[0-9]{9}|GR[0-9]{9}|HU[0-9]{8}|IE[0-9]{7}[A-Z]{1,2}|IT[0-9]{11}|LV[0-9]{11}|LT[0-9]{9}|LU[0-9]{8}|MT[0-9]{8}|NL[0-9]{9}B[0-9]{2}|PL[0-9]{10}|PT[0-9]{9}|RO[0-9]{2,10}|SK[0-9]{10}|SI[0-9]{8}|ES[0-9A-Z][0-9]{7}[0-9A-Z]|SE[0-9]{12}|XI[0-9]{9})\b", "[REDACTED_VAT]", "redact", ("GDPR",)),
    # IBAN: tolerate optional single intra-group spaces so a PRINTED grouped IBAN
    # ("DE89 3704 0044 0532 0130 00") is caught, not just the contiguous form (a
    # recall gap). The leading lookahead requires >=5 digits within a bounded
    # window (every real IBAN's BBAN is digit-dense), so a run of all-letter 4-char
    # prose "groups" ("GB12 then text here") can no longer be absorbed across word
    # boundaries. The density check is written as five literal \d anchors separated
    # by bounded [A-Z0-9 ]{0,6} char classes — deliberately NOT a quantified group
    # like (?:[A-Z ]*\d){5}, which the gateway/serializer ReDoS heuristic rejects
    # as a nested unbounded quantifier (and which would silently fail-open). See
    # _PRIORITY_OVERRIDES below: IBAN is lifted above PHONE_GENERIC so a spaced
    # IBAN redacts as IBAN, not phone.
    ("IBAN", "IBAN", r"\b(?=[A-Z0-9 ]{0,6}\d[A-Z0-9 ]{0,6}\d[A-Z0-9 ]{0,6}\d[A-Z0-9 ]{0,6}\d[A-Z0-9 ]{0,6}\d)[A-Z]{2}\d{2}(?:[A-Z0-9]{11,30}|(?:\s[A-Z0-9]{4}){2,7}(?:\s[A-Z0-9]{1,3})?)\b", "[REDACTED_IBAN]", "redact", ("GDPR", "PCI")),
    # SWIFT/BIC: true shape = 4-letter bank + 2-LETTER country + 2 alnum location
    # (+ optional 3 alnum branch). The engine compiles all rule regexes with
    # re.IGNORECASE, so a bare all-letter 8-char token (e.g. "DEUTDEFF") is
    # indistinguishable from ordinary prose ("verbatim", "absolute"). We match a
    # token only via a prose-free signal: (A) a SWIFT/BIC label, (B) a digit in the
    # location/branch suffix (no English word has one), or (C) the standard XXX
    # branch code. The middle branch previously used [A-Z]{6} for bank+country,
    # which let any 6-letter prefix through; it now enforces 4 letters + a 2-LETTER
    # country code so a 4-letter+digits invoice ref ("ABCD1234") cannot match.
    ("SWIFT_BIC", "SWIFT/BIC code", r"\b(?:SWIFT|BIC)(?:\s*(?:code|number|no\.?))?[:#\s]+[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?\b|\b[A-Z]{4}[A-Z]{2}(?=[A-Z0-9]{2}(?:[A-Z0-9]{3})?\b)(?=[A-Z0-9]*\d)[A-Z0-9]{2}(?:[A-Z0-9]{3})?\b|\b[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}XXX\b", "[REDACTED_BIC]", "redact", ("PCI",)),
    # CA_SIN / AU_TFN: a bare 9-digit run (XXX XXX XXX) is indistinguishable from
    # a tracking / order / part number, so the old patterns triple-redacted any
    # 9-digit token. A 9-digit run carries no signal that it is a national ID;
    # require an explicit SIN / social-insurance (CA) or TFN / tax-file (AU) label
    # before the digits, mirroring DE_STEUER_ID / US_SSN_LABELLED.
    ("CA_SIN", "Canada Social Insurance Number", r"\b(?:SIN|social\s+insurance(?:\s*(?:no|number|#))?)[:#\s]+\d{3}[-\s]?\d{3}[-\s]?\d{3}\b", "[REDACTED_SIN]", "redact", ("GDPR",)),
    ("AU_TFN", "Australia Tax File Number", r"\b(?:TFN|tax\s+file\s+(?:no|number|#)?)[:#\s]+\d{3}\s?\d{3}\s?\d{3}\b", "[REDACTED_TFN]", "redact", ("GDPR",)),
    # DE_STEUER_ID: the old \b\d{11}\b matched ANY bare 11-digit number (phone
    # fragments, order numbers, timestamps). A bare 11-digit run carries no signal
    # that it is a tax ID, so require an explicit steuer-id / tax-id / TIN label.
    ("DE_STEUER_ID", "Germany tax ID (11 digits)", r"\b(?:steuer(?:\s*[\-]?\s*id(?:entifikationsnummer)?)?|tax[\s\-]?id(?:entification)?(?:\s*(?:no|number))?|tin)[:#\s]+\d{11}\b", "[REDACTED_TAX_ID]", "redact", ("GDPR",)),
    ("ES_DNI_NIE", "Spain DNI/NIE", r"\b[XYZ]?\d{7,8}[A-Z]\b", "[REDACTED_DNI]", "redact", ("GDPR",)),
    ("IT_CODICE_FISCALE", "Italy Codice Fiscale", r"\b[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]\b", "[REDACTED_CF]", "redact", ("GDPR",)),
    # --- India ---
    ("AADHAAR", "India Aadhaar (12 digits)", r"(?<!\d)(?<!\d\s)\b\d{4}\s?\d{4}\s?\d{4}\b(?!\s?\d{4})", "[REDACTED_AADHAAR]", "redact", ("GDPR",)),
    ("IN_PAN", "India PAN", r"\b[A-Z]{5}\d{4}[A-Z]\b", "[REDACTED_PAN]", "redact", ("GDPR",)),
    # IN_VOTER_ID: the old \b[A-Z]{3}\d{7}\b matched any 3-letter + 7-digit token,
    # i.e. ordinary invoice/SKU/order refs ("ABC1234567"). Require a voter-id /
    # EPIC label so only the labeled identifier matches.
    ("IN_VOTER_ID", "India Voter ID", r"\b(?:voter\s*(?:id)?|epic(?:\s*(?:no|number|id))?)[:#\s]+[A-Z]{3}\d{7}\b", "[REDACTED_VOTER]", "redact", ("GDPR",)),
    ("IN_DRIVING_LICENSE", "India driving license", r"\b[A-Z]{2}[\-\s]?\d{2}\s?\d{4}\s?\d{7}\b", "[REDACTED_DL]", "redact", ("GDPR",)),
    ("IN_PASSPORT", "India passport (labeled)", r"\b(?:passport|passport\s+no|passport\s+number)[:\s#]*[A-Z]\d{7}\b", "[REDACTED_PASSPORT]", "redact", ("GDPR",)),
    # --- PCI ---
    ("CREDIT_CARD", "Credit card (grouped)", r"\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b", "[REDACTED_CARD]", "redact", ("PCI-DSS",)),
    ("CREDIT_CARD_LUHN_CANDIDATE", "Credit card (13–19 digits)", r"\b(?:\d[ -]*?){13,19}\b", "[REDACTED_CARD]", "redact", ("PCI-DSS",)),
    ("CARD_CVV", "Card CVV/CVC", r"\b(?:CVV|CVC|CID)[:\s#]*\d{3,4}\b", "[REDACTED_CVV]", "redact", ("PCI-DSS",)),
    ("CARD_EXPIRY", "Card expiry (MM/YY)", r"\b(?:exp(?:iry)?|valid\s+thru)[:\s]*\d{2}[/\-]\d{2,4}\b", "[REDACTED_EXPIRY]", "redact", ("PCI-DSS",)),
    ("US_BANK_ACCOUNT", "US bank account (labeled)", r"\b(?:account|routing)\s*#?\s*[:\s]*\d{8,17}\b", "[REDACTED_BANK]", "redact", ("PCI-DSS",)),
    # US_ROUTING_NUMBER: a bare 9-digit run matched ANY 9-digit tracking / order /
    # reference number (a triple-redact alongside CA_SIN/AU_TFN). Require a
    # routing / ABA / RTN label before the 9 digits so only the labeled bank
    # routing number redacts.
    ("US_ROUTING_NUMBER", "US ABA routing number", r"\b(?:routing|aba|rtn)(?:\s*(?:no|number|#))?[:#\s]+\d{9}\b", "[REDACTED_ROUTING]", "redact", ("PCI-DSS",)),
    # --- PHI ---
    ("MEDICAL_NPI_DEA", "NPI / DEA number", r"\b(?:NPI|DEA)\s*#?\s*\d{7,10}\b", "[REDACTED_MEDICAL_ID]", "redact", ("HIPAA",)),
    ("MEDICAL_MRN", "Medical record number", r"\b(?:MRN|medical\s+record)\s*#?\s*\d{6,10}\b", "[REDACTED_MRN]", "redact", ("HIPAA",)),
    ("INSURANCE_MEMBER_ID", "Insurance member ID", r"\b(?:insurance\s+(?:id|number|#)|member\s+id|policy\s*#)\s*[:\s]*[A-Z0-9]{5,15}\b", "[REDACTED_INSURANCE]", "redact", ("HIPAA",)),
    ("HEALTH_PLAN_ID", "Health plan beneficiary ID", r"\b(?:beneficiary|subscriber)\s*(?:id|#)[:\s]*[A-Z0-9]{6,14}\b", "[REDACTED_HEALTH_ID]", "redact", ("HIPAA",)),
    # --- Network & device ---
    # IP_V4: a dotted quad also matches 4-part software version / build numbers
    # ("version 1.2.3.4", "build 10.0.19041.1", "v1.2.3.4"), redacting them as IPs.
    # Add a negative-context guard: a set of FIXED-WIDTH negative lookbehinds that
    # reject the quad when it is immediately preceded by a version/build/v marker.
    # Each lookbehind starts with \b so a marker substring inside another word
    # ("ser-VER ") cannot trigger it, preserving real-IP recall ("server 10.0.0.1"
    # still matches). The leading/trailing (?<![\w.]) / (?![\w.]) keep the quad a
    # standalone token (not a fragment of a longer dotted run).
    ("IP_V4", "IPv4 address", r"(?<![\w.])(?<!\bversion )(?<!\bversion=)(?<!\bbuild )(?<!\bbuild=)(?<!\bver )(?<!\bv)(?<!\bv\.)(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?![\w.])", "[REDACTED_IP]", "redact", ("NIST",)),
    # IP_V6: the old pattern only matched the fully-expanded 8-group form and
    # silently missed every RFC 5952 compressed address ("2001:db8::1", "fe80::",
    # "::1") — a recall gap. Add branches for the compressed "::" forms: trailing
    # "::" (groups then ::), an embedded "::" (head groups :: tail groups), and a
    # leading "::" (::x...). The leading/trailing (?<![\w:]) / (?![\w:]) guards keep
    # each match a standalone address so ordinary colon-separated prose
    # ("3:4:5:6", "time 12:34:56") and a bare "::" in text are not absorbed.
    ("IP_V6", "IPv6 address", r"\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b|(?<![\w:])(?:[0-9a-fA-F]{1,4}:){1,7}:(?![\w:])|(?<![\w:])(?:[0-9a-fA-F]{1,4}:){1,6}(?::[0-9a-fA-F]{1,4}){1,6}\b|(?<![\w:]):(?::[0-9a-fA-F]{1,4}){1,7}(?![\w:])", "[REDACTED_IP]", "redact", ("NIST",)),
    ("MAC_ADDRESS", "MAC address", r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b", "[REDACTED_MAC]", "redact", ("NIST",)),
    ("IMEI", "IMEI (15 digits)", r"\b\d{15}\b", "[REDACTED_IMEI]", "redact", ("GDPR",)),
    # VIN: the old \b[A-HJ-NPR-Z0-9]{17}\b matched ANY 17-char alphanumeric token,
    # i.e. commit hashes / fingerprints / 17-digit numbers. Require a VIN/vehicle
    # label AND (via lookaheads) that the 17-char body mixes letters and digits, so
    # an all-digit or all-alpha 17-char string is excluded even when labeled.
    ("VIN", "Vehicle VIN", r"\b(?:VIN|vehicle\s+identification\s+(?:no|number)|vehicle\s*(?:no|number|id))[:#\s]+(?=[A-HJ-NPR-Z0-9]{17}\b)(?=[A-HJ-NPR-Z0-9]*\d)(?=[A-HJ-NPR-Z0-9]*[A-HJ-NPR-Z])[A-HJ-NPR-Z0-9]{17}\b", "[REDACTED_VIN]", "redact", ("CCPA",)),
    ("URL", "HTTP(S) URL", r"\bhttps?://[^\s<>\"']+\b", "[REDACTED_URL]", "redact", ("GDPR",)),
    ("URL_WITH_CREDENTIALS", "URL with embedded credentials", r"\bhttps?://[^\s:@/]+:[^\s@/]+@[^\s<>\"']+\b", "[REDACTED_URL]", "block", ("NIST", "PCI")),
    # --- Crypto ---
    ("CRYPTO_BTC", "Bitcoin address", r"\b[13][a-km-zA-HJ-NP-Z1-9]{25,34}\b", "[REDACTED_CRYPTO]", "redact", ("GDPR",)),
    # CRYPTO_ETH: a bare 0x + 40 hex also matches non-PII 40-hex object ids that
    # happen to carry a 0x prefix (memory addresses, hashed blobs, dumped digests)
    # — redacting them as crypto. Require a prose-free signal: (A) an
    # eth/ethereum/wallet (or send-to/from-transfer) label before the address, or
    # (B) the ENS ".eth" suffix after it. A bare unlabeled 0x+40hex no longer
    # redacts on its own.
    ("CRYPTO_ETH", "Ethereum address", r"\b(?:eth(?:ereum)?(?:\s*(?:address|addr|wallet))?|wallet|to|from)[:#\s]+0x[a-fA-F0-9]{40}\b|\b0x[a-fA-F0-9]{40}\.eth\b", "[REDACTED_CRYPTO]", "redact", ("GDPR",)),
    ("CRYPTO_BECH32", "Bitcoin bech32 address", r"\bbc1[a-z0-9]{39,59}\b", "[REDACTED_CRYPTO]", "redact", ("GDPR",)),
    # --- Secrets & credentials (block or redact) ---
    ("OPENAI_API_KEY", "OpenAI API key", r"\bsk-[a-zA-Z0-9]{20,}\b", "[REDACTED_SECRET]", "block", ("NIST",)),
    ("ANTHROPIC_API_KEY", "Anthropic API key", r"\bsk-ant-[a-zA-Z0-9\-]{20,}\b", "[REDACTED_SECRET]", "block", ("NIST",)),
    ("AWS_ACCESS_KEY", "AWS access key ID", r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA)[0-9A-Z]{16}\b", "[REDACTED_AWS_KEY]", "block", ("NIST",)),
    ("AWS_SECRET_KEY", "AWS secret access key (labeled)", r"\b(?:aws)?[_\s-]?secret[_\s-]?access[_\s-]?key[\"'\s:=]+[A-Za-z0-9/+=]{40}\b", "[REDACTED_AWS_SECRET]", "block", ("NIST",)),
    ("GITHUB_TOKEN", "GitHub token", r"\bgh[pousr]_[a-zA-Z0-9]{36,}\b", "[REDACTED_SECRET]", "block", ("NIST",)),
    ("SLACK_TOKEN", "Slack token", r"\bxox[bpas]-[0-9]{10,}-[a-zA-Z0-9\-]+\b", "[REDACTED_SECRET]", "block", ("NIST",)),
    ("GCP_API_KEY", "Google API key", r"\bAIza[0-9A-Za-z\-_]{35}\b", "[REDACTED_SECRET]", "block", ("NIST",)),
    ("STRIPE_KEY", "Stripe secret key", r"\b(?:sk|rk)_(?:live|test)_[0-9a-zA-Z]{20,}\b", "[REDACTED_SECRET]", "block", ("PCI", "NIST")),
    ("JWT", "JSON Web Token", r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b", "[REDACTED_JWT]", "block", ("NIST",)),
    ("BEARER_TOKEN", "Bearer / generic token", r"\b(?:Bearer|token)\s+[A-Za-z0-9_\-\.]{20,}\b", "[REDACTED_SECRET]", "block", ("NIST",)),
    ("GENERIC_API_KEY", "Generic API key assignment", r"(?:api[_\-]?key|apikey)[\"\s:=]+[A-Za-z0-9_\-\.]{16,}", "[REDACTED_SECRET]", "block", ("NIST",)),
    ("API_KEY_PREFIXED", "Prefixed API tokens (sk/pk/rk/ghp)", r"\b(?:sk|pk|rk|api|key|tok|ghp|gho|xox[baprs])[-_][A-Za-z0-9]{16,}\b", "[REDACTED_SECRET]", "block", ("NIST",)),
    ("AZURE_CONNECTION_STRING", "Azure storage connection string", r"\bDefaultEndpointsProtocol=https;AccountName=[^;]+;AccountKey=[A-Za-z0-9+/=]{40,}", "[REDACTED_AZURE]", "block", ("NIST",)),
    ("PRIVATE_KEY_PEM", "PEM private key header", r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----", "[REDACTED_PRIVATE_KEY]", "block", ("NIST",)),
    ("SSH_PRIVATE_KEY", "OpenSSH private key", r"-----BEGIN OPENSSH PRIVATE KEY-----", "[REDACTED_PRIVATE_KEY]", "block", ("NIST",)),
    ("PASSWORD_IN_CONFIG", "Password in config line", r"(?:password|passwd|pwd)[\"\s:=]+[^\s\"']{8,}", "[REDACTED_PASSWORD]", "block", ("NIST",)),
    ("DATABASE_URL", "Database connection URL with password", r"\b(?:postgres|mysql|mongodb)(?:\+[a-z]+)?://[^\s:@/]+:[^\s@/]+@[^\s<>\"']+\b", "[REDACTED_DB_URL]", "block", ("NIST", "PCI")),
    ("STUDENT_ID", "Student ID (labeled)", r"\b(?:student\s+id|sid)[:\s#]*[A-Z0-9]{5,12}\b", "[REDACTED_STUDENT_ID]", "redact", ("FERPA",)),
    ("EMPLOYEE_ID", "Employee ID (labeled)", r"\b(?:employee\s+id|emp\s*id|badge\s*#)[:\s#]*[A-Z0-9]{4,12}\b", "[REDACTED_EMPLOYEE_ID]", "redact", ("GDPR",)),
]


def _validate_catalog() -> None:
    seen: set[str] = set()
    for entity_key, _name, pattern, _repl, action, _fw in _RULE_SPECS:
        if entity_key in seen:
            raise ValueError(f"duplicate entity_key in catalog: {entity_key}")
        seen.add(entity_key)
        re.compile(pattern)
        if action not in {"redact", "block", "monitor"}:
            raise ValueError(f"invalid action for {entity_key}: {action}")
    if len(_RULE_SPECS) < MIN_RULE_COUNT:
        raise ValueError(f"catalog must define at least {MIN_RULE_COUNT} rules, got {len(_RULE_SPECS)}")


_validate_catalog()


def policy_code_for_org(org_id: int) -> str:
    return f"PII_PKG_{org_id}"


# Priority is derived from list position (earlier => higher), but a few rules need
# an explicit lift to win redaction-attribution ties at the gateway. Hints are
# applied in -priority order (see compiler._build_snapshot order_by("-priority")),
# so a spaced IBAN ("DE89 3704 ...") must be redacted as [REDACTED_IBAN] BEFORE
# PHONE_GENERIC can mask its digit groups as [REDACTED_PHONE]. We bump IBAN above
# PHONE_GENERIC's derived priority. Keyed by entity_key; value is an absolute
# priority that overrides ``total - idx``.
_PRIORITY_OVERRIDES: dict[str, int] = {
    # PHONE_GENERIC sits at list index 6 (priority total-6 == 56-6 == derived);
    # IBAN must rank strictly higher. Use a value safely above any derived
    # priority (total - 0 == total) so IBAN leads regardless of future reordering.
    "IBAN": len(_RULE_SPECS) + 10,
}


def build_rule_dicts() -> list[dict[str, Any]]:
    """Return rule payloads suitable for Rule bulk_create / JSON fixtures."""
    rules: list[dict[str, Any]] = []
    total = len(_RULE_SPECS)
    for idx, (entity_key, label, pattern, replacement, action, frameworks) in enumerate(_RULE_SPECS):
        rules.append(
            {
                "name": label,
                "rule_type": "regex",
                "condition": {
                    "regex": pattern,
                    "field": "both",
                    "entity_key": entity_key,
                    "package_id": PACKAGE_ID,
                },
                "action": action,
                "redaction_config": {"replacement": replacement},
                "priority": _PRIORITY_OVERRIDES.get(entity_key, total - idx),
                "enabled": True,
                "description": (
                    f"PII package rule ({entity_key}). Frameworks: {', '.join(frameworks)}."
                ),
            }
        )
    return rules


def package_metadata() -> dict[str, Any]:
    return {
        "package_id": PACKAGE_ID,
        "package_version": PACKAGE_VERSION,
        "rule_count": len(_RULE_SPECS),
        "standards": ["GDPR", "CCPA", "HIPAA", "PCI-DSS", "NIST-PII", "FERPA"],
        "source": "policy.pii_policy_catalog",
    }
