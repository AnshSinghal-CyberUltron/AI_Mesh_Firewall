#!/usr/bin/env bash
# Regression (live): the MCP gateway must resolve the tenant from the
# AUTHENTICATED gateway key, never the client-supplied URL org_slug.
#   - own org        -> NOT 403 (passes org scope; may 404 if server slug absent)
#   - other org URL  -> 403 org_scope_violation (cross-tenant blocked)
#   - no key         -> 401
#   - invalid key    -> 401
# Any cross-tenant success is a CRITICAL isolation breach.
#
# Mints a throwaway org-3 gateway key via the control container, probes, revokes.
# Usage: bash tests/e2e/mcp_sandbox_adversarial/mcp_cross_tenant_authz.sh
set -u

GW="${GATEWAY_URL:-http://localhost:8300}"
CONTROL_CONTAINER="${CONTROL_CONTAINER:-aimesh_gate-control-1}"
OWN_ORG="${OWN_ORG:-zeroshield}"      # org id 3 slug
OTHER_ORG="${OTHER_ORG:-acme-test}"   # a DIFFERENT org's slug
PROJ="iter6-authz-probe-live"

fail() { echo "FAIL mcp_cross_tenant_authz (CRITICAL): $1"; revoke; exit 1; }
revoke() {
  docker exec -w /app/control "$CONTROL_CONTAINER" .venv/bin/python manage.py shell -c "
from core.models import GatewayAPIKey
GatewayAPIKey.objects.filter(project_id='$PROJ').delete()
" >/dev/null 2>&1
}

# Mint a throwaway key bound to org 3 (raw printed once, captured, never echoed).
RAW=$(docker exec -w /app/control "$CONTROL_CONTAINER" .venv/bin/python manage.py shell -c "
from core.models import GatewayAPIKey
from mcp_connector.models import MCPServerRegistration as M
from django.contrib.auth import get_user_model
U=get_user_model()
org=M.objects.filter(organization_id=3).first().organization
owner=U.objects.filter(is_superuser=True).first() or U.objects.first()
inst,raw=GatewayAPIKey.generate_key(name='iter6-authz-probe-live', owner=owner, project_id='$PROJ')
inst.organization=org; inst.save()
print('RAWKEY='+raw)
" 2>/dev/null | grep '^RAWKEY=' | cut -d= -f2)
[ -n "$RAW" ] || fail "could not mint test key"

status() { curl -s -m 10 -o /tmp/authz_pr.json -w "%{http_code}" "$@"; }
errof()  { python3 -c 'import json;print(json.load(open("/tmp/authz_pr.json")).get("error","?"))' 2>/dev/null || echo "?"; }

S=$(status -H "Authorization: Bearer $RAW" "$GW/gateway/$OWN_ORG/mcp/playwright/tools")
[ "$S" != "403" ] || fail "own-org request wrongly 403'd (err=$(errof))"
echo "  own_org($OWN_ORG): HTTP $S (not 403 -> org scope passed)"

S=$(status -H "Authorization: Bearer $RAW" "$GW/gateway/$OTHER_ORG/mcp/playwright/tools"); E=$(errof)
{ [ "$S" = "403" ] && [ "$E" = "org_scope_violation" ]; } || fail "cross-tenant not blocked: HTTP $S err=$E"
echo "  cross_tenant($OTHER_ORG): HTTP $S err=$E"

S=$(status "$GW/gateway/$OWN_ORG/mcp/playwright/tools")
[ "$S" = "401" ] || fail "no-auth expected 401, got $S"
echo "  no_auth: HTTP $S"

S=$(status -H "Authorization: Bearer sk-invalid-000" "$GW/gateway/$OWN_ORG/mcp/playwright/tools")
[ "$S" = "401" ] || fail "invalid-key expected 401, got $S"
echo "  invalid_key: HTTP $S"

revoke
echo "PASS mcp_cross_tenant_authz: tenant resolved from key; cross-tenant 403; unauth 401 (fail closed)"
