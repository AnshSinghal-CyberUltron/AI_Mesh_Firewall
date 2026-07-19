# MCP action scenarios — live summary

- Org: `zeroshield`
- Passed: **8/8**
- All green: `True`

## By action

| Action | Pass | Fail | Total |
|--------|------|------|-------|
| allow | 2 | 0 | 2 |
| block | 0 | 0 | 0 |
| redact | 4 | 0 | 4 |
| flag | 2 | 0 | 2 |

## Scenarios

- **PASS** `allow` `everything-1__allow_echo` — benign tool call succeeded
- **PASS** `redact` `everything-1__redact_ssn` — SSN masked in egress
- **PASS** `redact` `everything-1__redact_pem` — PEM redacted in egress
- **PASS** `flag` `everything-1__flag_aws_tag` — observe/tag path decision=allow tags=[]
- **PASS** `allow` `linear-manual-oauth__allow_list_teams` — benign tool call succeeded
- **PASS** `redact` `linear-manual-oauth__redact_ssn_query` — no raw SSN in egress (query may not echo); call allowed
- **PASS** `redact` `linear-manual-oauth__redact_pem_query` — PEM header absent from egress (acceptable)
- **PASS** `flag` `linear-manual-oauth__flag_aws_query` — decision=allow tags=[]
