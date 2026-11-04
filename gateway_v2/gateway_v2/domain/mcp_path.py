"""MCP route path grammar (codes only; no framework).

The org segment is a control org slug: lower-case alphanumerics, `_` and `-`, 1..63 characters,
starting with an alphanumeric.
"""

from __future__ import annotations

import re

ORG_SEGMENT = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
