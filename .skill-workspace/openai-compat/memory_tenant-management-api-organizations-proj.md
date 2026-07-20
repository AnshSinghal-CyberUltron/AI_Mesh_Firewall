# Tenant Management API (Organizations, Projects, API Keys, Members) — OpenAI-Compatible endpoints for multi-tenant governance and key lifecycle

## Current
ZeroShield currently has:
1. **Auth Models** (control/ai_mesh_control/auth/models.py:L6-L141):
   - Organization(name, slug, is_active, metadata) — multi-tenant container
   - UserProfile(user, organization, roles, is_platform_operator) — org-scoped user
   - TerminatedSession — session revocation tracking

2. **Gateway API Key Model** (control/ai_mesh_control/core/models.py:L281-L480):
   - GatewayAPIKey(organization, prefix, key_hash, name, owner, project_id, permissions, allowed_models, rate_limit_tpm, risk_score, max_context_tokens, is_active, expires_at)
   - Methods: generate_key(), ensure_default_for_org(), hash_raw_key()
   - Redis sync on save/delete (post_save signal pushes identity to Redis at auth:apikey:{key_hash})

3. **Gateway API Key Endpoints** (control/ai_mesh_control/core/gateway_key_views.py:L289-L352 &amp; core/gateway_urls.py:L19-L40):
   - GatewayAPIKeyViewSet (DRF ModelViewSet)
   - Routes: GET /api/gateways/keys/ (list), POST /api/gateways/keys/ (create), GET /api/gateways/keys/{id}/ (retrieve), PATCH /api/gateways/keys/{id}/ (update), DELETE /api/gateways/keys/{id}/ (revoke)
   - Serializers: GatewayAPIKeyCreateSerializer, GatewayAPIKeySerializer, GatewayAPIKeyUpdateSerializer
   - Permission: IsAuthenticated + IsGatewayKeyOwner (object-level)

4. **Admin/Proxy Views** (control/ai_mesh_control/core/admin_urls.py):
   - CircuitBreakerStateProxyView, GatewayRagCollectionsProxyView, GatewayDbTestProxyView — proxy to gateway /v1/admin/* endpoints
   - These model the pattern of Django-gated server-side proxies for admin operations

5. **Auth Endpoints** (control/ai_mesh_control/auth/urls.py &amp; auth/views.py):
   - /api/auth/token/ (login), /api/auth/token/refresh/, /api/auth/token/verify/, /api/auth/me/, /api/auth/change-password/, /api/auth/sessions/terminate/, /api/auth/users/ (CRUD, requires admin)
   - No dedicated org/project management endpoints yet

6. **Gateway Middleware Auth** (gateway/ai_mesh_gateway/middleware.py:L61-L200):
   - AuthContext (key_id, project_id, organization_id, org_slug, permissions, allowed_models, rate_limit_tpm, risk_score, roles, is_active, expires_at) — injected into request.state
   - validate_api_key() reads from Redis (auth:apikey:{hash}), validates is_active and expires_at
   - GatewayAPIKey.build_redis_payload() syncs identity payload to Redis on create/update

7. **OpenAI-Compatible Data Plane** (gateway/ai_mesh_gateway/main.py:L3484, L7169, L10411):
   - POST /v1/chat/completions, POST /v1/embeddings, GET /v1/models
   - All route through AuthMiddleware and inject AuthContext
   - Enforce firewall pipeline (scanner.py, output_guard.py, pipeline_trace.py) with zeroshield response metadata
   - Support multi-model routing and per-tenant model restrictions (allowed_models field)

**MISSING: OpenAI-style org/project/admin API that operators can consume programmatically**

## OpenAI Spec
OpenAI Admin/Organization API (public spec at https://platform.openai.com/docs/admin):
- POST /organization/projects — create project in organization
- GET /organization/projects — list projects
- GET /organization/projects/{project_id} — get project details
- PATCH /organization/projects/{project_id} — update project (name, status)
- DELETE /organization/projects/{project_id} — delete project
- POST /organization/projects/{project_id}/users — add user to project
- GET /organization/projects/{project_id}/users — list project members
- DELETE /organization/projects/{project_id}/users/{user_id} — remove user from project
- PATCH /organization/projects/{project_id}/users/{user_id} — update member role
- GET /organization/projects/{project_id}/api_keys — list project API keys
- POST /organization/projects/{project_id}/api_keys — create API key for project
- PATCH /organization/projects/{project_id}/api_keys/{key_id} — rotate/update key (e.g., enable/disable)
- DELETE /organization/projects/{project_id}/api_keys/{key_id} — revoke key
- GET /organization/users — list org users
- POST /organization/users — invite user to org
- PATCH /organization/users/{user_id} — update member role in org

Response shape (example from OpenAI docs):
```json
{
  "id": "proj_123abc",
  "object": "organization.project",
  "name": "My Project",
  "status": "active",
  "created_at": 1234567890,
  "archived_at": null,
  "api_key_ids": ["sk_live_..."],
  "updated_at": 1234567890
}
```

API Key response:
```json
{
  "id": "key_123",
  "object": "organization.api_key",
  "name": "My API Key",
  "created_at": 1234567890,
  "last_used_at": 1234567890,
  "redacted_value": "sk_live_...***",
  "owner": {
    "type": "user",
    "user": {...}
  }
}
```

## Reusable hooks
**Firewall Pipeline Hooks (existing, reusable by tenant-mgmt endpoints):**

1. **Post-Save Signal** (control/ai_mesh_control/core/signals.py):
   - GatewayAPIKey.post_save → Redis sync (auth:apikey:{key_hash})
   - Trigger on OrganizationSettings.post_save to flush config cache (if settings affect routing)
   - Already implemented for GatewayAPIKey; extend for new models

2. **Post-Delete Signal**:
   - GatewayAPIKey.post_delete → Redis flush (auth:apikey:{key_hash})
   - Call on DELETE /api/organization/projects/{id} and DELETE /api/organization/api_keys/{id} to evict Redis
   - Same pattern as gateway_key_views; no new code needed

3. **Auth Context Injection** (gateway/ai_mesh_gateway/middleware.py:L61-L82):
   - AuthContext class with key_id, project_id, organization_id, org_slug, permissions, allowed_models, rate_limit_tpm, risk_score
   - New tenant-mgmt endpoints inherit request.user.profile.organization isolation (no reuse of middleware; Django user auth instead)
   - But if future feature requires gateway to enforce org-level policies (e.g., org budget), extend AuthContext.monthly_budget and validate on hot path

4. **Permission Classes** (control/ai_mesh_control/core/gateway_key_views.py:L22-L42):
   - IsGatewayKeyOwner — object-level permission (has_object_permission checks ownership or admin role)
   - Reuse pattern for org-mgmt: create IsOrgMember + IsOrgAdmin permission classes
   - Existing codebase already has auth.models.is_platform_operator() function; reuse for platform-operator bypass

5. **Serializer Validation** (control/ai_mesh_control/core/gateway_serializers.py:L19-L62):
   - _strip_privileged_permission_flags() — validates request context, fails closed
   - Pattern: check user is platform operator before allowing privileged flags
   - Reuse in tenant-mgmt serializers: validate org admin status before allowing role updates

6. **Redis Key Schema** (gateway/ai_mesh_gateway/middleware.py:L126):
   - auth:apikey:{key_hash} → JSON payload for auth lookups
   - Extend schema for project policies: policy:org:{org_slug}:project:{project_id} → allowed_models list
   - One-line change: add Redis.get(f"policy:org:{org_slug}:project:{project_id}") in main.py model validation

7. **Error Response Format** (main.py:L401-L450):
   - _build_safe_block_response(), _build_block_response() — OpenAI-shaped {error, message, code}
   - New control-plane endpoints must use same envelope for consistency
   - One DRF utility function openai_error_response(code, message, status_code) is sufficient

## Gaps
[
  {
    "title": "OpenAI-Compatible Organization/Project Management API (missing POST/GET /organization/projects, /organization/users)",
    "severity": "Critical",
    "detail": "ZeroShield has internal Organization/UserProfile models and GatewayAPIKey management, but no OpenAI-compatible endpoints for operators to create projects, list members, or manage cross-org tenants via standard OpenAI SDK patterns. Current /api/gateways/keys/ endpoints are ZeroShield-proprietary, not OpenAI-shaped.",
    "files": "control/ai_mesh_control/core/models.py:L6-L141 (Organization/UserProfile), L281-L480 (GatewayAPIKey); control/ai_mesh_control/core/gateway_key_views.py (ViewSet); control/ai_mesh_control/auth/urls.py (auth endpoints only, no org mgmt)",
    "implementationApproach": "Create NEW routers/serializers following OpenAI admin API contract. Reuse existing Django ORM models (Organization, UserProfile, GatewayAPIKey) and the firewall pipeline auth context injection pattern:\n\n1. **New URLs** at /api/organization/ (mirror OpenAI shape):\n   - GET /api/organization/projects \u2014 list projects (DRF generic ListAPIView, filter by user org)\n   - POST /api/organization/projects \u2014 create project (CreateAPIView + GatewayAPIKey.generate_key() post-action)\n   - GET /api/organization/projects/{project_id} \u2014 retrieve (RetrieveAPIView, org-scoped)\n   - PATCH /api/organization/projects/{project_id} \u2014 update (name, status via partial_update)\n   - DELETE /api/organization/projects/{project_id} \u2014 soft-delete or hard-delete (trigger post_delete signal to flush Redis)\n   - GET /api/organization/projects/{project_id}/api_keys \u2014 list keys for project (filter by project_id)\n   - POST /api/organization/projects/{project_id}/api_keys \u2014 mint new key (POST to existing GatewayAPIKeyViewSet but project-scoped)\n   - PATCH /api/organization/projects/{project_id}/api_keys/{key_id} \u2014 toggle is_active, update perms\n   - DELETE /api/organization/projects/{project_id}/api_keys/{key_id} \u2014 revoke (same as /api/gateways/keys/{id}/, just project-scoped route)\n   - GET /api/organization/users \u2014 list org members (UserProfile.objects.filter(organization=user.org))\n   - PATCH /api/organization/users/{user_id} \u2014 update role (permissions: owner only, or org admin)\n\n2. **New Serializers** (control/ai_mesh_control/core/organization_serializers.py):\n   - ProjectSerializer (id, name, status, created_at, updated_at, api_key_ids=SerializerMethodField)\n   - ProjectCreateSerializer (name only, generate project_id server-side)\n   - APIKeyAdminSerializer (mirrors GatewayAPIKeySerializer but redacted_value instead of prefix)\n   - OrganizationMemberSerializer (user id/email, role)\n\n3. **New ViewSets** (control/ai_mesh_control/core/organization_views.py):\n   - ProjectViewSet(ModelViewSet): queryset=Project.objects.filter(org=user.org), permissions=[IsAuthenticated, IsOrgMember]\n   - APIKeyAdminViewSet: filtered by project_id (query param), reuse GatewayAPIKey model + permissions\n   - OrganizationMemberViewSet: UserProfile.objects.filter(organization=user.org), PATCH allows role update only for org admin\n\n4. **Reuse Firewall Auth Pipeline**:\n   - These endpoints enforce org-scoping via the same `request.user.profile.organization` pattern that gateway_key_views uses\n   - POST /api/organization/projects/{proj_id}/api_keys calls GatewayAPIKey.generate_key() (existing method) + post_save signal (already pushes to Redis)\n   - DELETE endpoint invokes post_delete signal (already flushes Redis)\n   - No custom enforcement logic needed \u2014 inherit from existing IsGatewayKeyOwner + org isolation patterns\n\n5. **Response Shape** \u2014 match OpenAI contract:\n   ```python\n   {\n     \"id\": \"proj_acme_001\",  # project_id from DB\n     \"object\": \"organization.project\",\n     \"name\": \"Acme RAG System\",\n     \"status\": \"active\",\n     \"created_at\": 1718000000,\n     \"api_key_ids\": [\"zs_xK9mQ...\"],  # list of key prefixes or IDs\n   }\n   ```\n   \n6. **Tangible Entry Points**:\n   - control/ai_mesh_control/core/organization_urls.py (new file, register in main_app/urls.py at path(\"api/organization/\", include(...)))\n   - control/ai_mesh_control/core/organization_views.py (new file)\n   - control/ai_mesh_control/core/organization_serializers.py (new file)\n   - Update core/models.py to add Project model or rename project_id to become a foreign key (or keep as string, depending on design)\n   - Existing GatewayAPIKey model is sufficient (already has organization FK and project_id field)",
    "effort": "M",
    "firewallRisk": "Low. New endpoints are **management-plane only** (no data-plane changes), behind IsAuthenticated + org-scoped queries. Permission model mirrors existing gateway_key_views: org members can list/view, only org admins or operators can mutate. Redis sync (post_save/post_delete signals) already in place for GatewayAPIKey \u2014 no new enforcement surface introduced. However, **critical**: ensure DELETE /api/organization/projects/{id} fires post_delete signal to flush Redis; test with live auth middleware to confirm key rejection is sub-second post-revocation."
  },
  {
    "title": "API Key Rotation Endpoint (missing PUT/POST /organization/projects/{proj_id}/api_keys/{key_id}/rotate)",
    "severity": "High",
    "detail": "OpenAI supports rotate operation (e.g., POST /organization/projects/{proj_id}/api_keys/{key_id}/rotate) which revokes old key and issues new one atomically. ZeroShield's PATCH /api/gateways/keys/{id}/ allows is_active=false (soft disable) but no atomic rotate. Operators cannot mint a key replacement without manual delete + create (race condition window).",
    "files": "control/ai_mesh_control/core/gateway_key_views.py:L224-L288 (partial_update only, no rotate action); control/ai_mesh_control/core/models.py:L393-L480 (GatewayAPIKey.generate_key() exists, no rotate method)",
    "implementationApproach": "1. Add rotate() classmethod to GatewayAPIKey (control/ai_mesh_control/core/models.py):\n   ```python\n   @classmethod\n   def rotate(cls, instance: 'GatewayAPIKey') -> tuple['GatewayAPIKey', str]:\n       \"\"\"Atomically revoke old key and issue new one with same perms/project.\"\"\"\n       old_hash = instance.key_hash\n       new_instance, new_plaintext = cls.generate_key(\n           name=instance.name,\n           owner=instance.owner,\n           project_id=instance.project_id,\n           permissions=instance.permissions,\n           allowed_models=instance.allowed_models,\n           rate_limit_tokens_per_minute=instance.rate_limit_tokens_per_minute,\n           ...\n       )\n       new_instance.organization = instance.organization\n       new_instance.save()\n       # Revoke old key (post_delete signal flushes Redis)\n       instance.delete()\n       return new_instance, new_plaintext\n   ```\n\n2. Add rotate action to GatewayAPIKeyViewSet (gateway_key_views.py) or new organization_views.py ProjectAPIKeyViewSet:\n   ```python\n   @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated, IsGatewayKeyOwner])\n   def rotate(self, request, pk=None):\n       instance = self.get_object()\n       new_key, plaintext = GatewayAPIKey.rotate(instance)\n       response_data = GatewayAPIKeySerializer(new_key).data\n       response_data['key'] = plaintext\n       return Response(response_data, status=status.HTTP_201_CREATED)\n   ```\n\n3. In organization_urls.py, expose at POST /api/organization/projects/{proj_id}/api_keys/{key_id}/rotate (DRF route decorator handles the action)\n\n4. **Firewall Risk**: Both old and new keys briefly exist; post_delete signal ensures old key is evicted from Redis within milliseconds. No window for dual-key exploit if signal fires before client receives response. Validate in tests: create key \u2192 call rotate \u2192 confirm old key 401s on gateway while new key 200s.",
    "effort": "S",
    "firewallRisk": "Low-to-Medium. Rotate is atomic in DB (transaction wraps generate + delete), but Redis eviction is async (post_delete signal). Brief race: old key could still validate if Redis read happens before signal flushes. Mitigation: post_delete signal is synchronous (not celery task), so timing is sub-millisecond. Test: mint key, call rotate, immediately hit gateway with old key \u2014 should 401. If concurrent test race, add DELETE ... RETURNING to capture old hash and explicitly flush Redis in view before returning 201."
  },
  {
    "title": "Organization Settings and Billing Endpoints (missing GET/PATCH /organization/settings, /organization/billing)",
    "severity": "Medium",
    "detail": "OpenAI exposes /organization/settings (billing email, notification preferences) and /organization/billing (usage, plan, invoices). ZeroShield has no org-level settings/billing surface. Operators cannot view aggregate token spend, configure per-org rate limits, or set budgets via API.",
    "files": "control/ai_mesh_control/auth/models.py:L6-L20 (Organization model has only name, slug, is_active, metadata); no settings/billing models",
    "implementationApproach": "1. Extend Organization model or create new OrganizationSettings model:\n   ```python\n   class OrganizationSettings(models.Model):\n       organization = OneToOneField(Organization, on_delete=CASCADE)\n       billing_email = CharField(max_length=255, blank=True)\n       notification_email = CharField(max_length=255, blank=True)\n       tier = CharField(choices=[('free', 'Free'), ('pro', 'Pro'), ('enterprise', 'Enterprise')], default='free')\n       monthly_budget_tokens = IntegerField(null=True, blank=True)  # for rate-limit controls\n       enforcement_mode = CharField(choices=[('allow', 'Allow'), ('warn', 'Warn'), ('block', 'Block')], default='allow')\n       created_at = DateTimeField(auto_now_add=True)\n       updated_at = DateTimeField(auto_now=True)\n   ```\n\n2. Create organization_settings_views.py with:\n   - GET /api/organization/settings (RetrieveAPIView, org-scoped, IsAuthenticated)\n   - PATCH /api/organization/settings (UpdateAPIView, org admin only)\n   - GET /api/organization/billing (ListAPIView, returns usage summary from EnforcementEvent/gateway telemetry)\n\n3. Billing endpoint queries gateway telemetry (Agent.metadata, EnforcementEvent.created_at) to compute daily/monthly token usage:\n   ```python\n   class BillingView(APIView):\n       def get(self, request):\n           org = request.user.profile.organization\n           usage = EnforcementEvent.objects.filter(\n               agent__endpoint__organization=org,\n               created_at__gte=datetime.now() - timedelta(days=30)\n           ).aggregate(\n               total_tokens=Sum('metadata__total_tokens'),  # if gateway logs tokens\n               blocked_count=Count('id', filter=Q(action='block'))\n           )\n           return Response({\n               'organization_id': org.id,\n               'current_month_tokens': usage['total_tokens'] or 0,\n               'monthly_budget': org.settings.monthly_budget_tokens,\n               'blocked_requests_month': usage['blocked_count'],\n               'estimated_cost': ...,\n           })\n   ```\n\n4. **Reuse firewall pipeline**: org-scoped queries (organization=user.org) inherit existing isolation. No data-plane changes.",
    "effort": "M",
    "firewallRisk": "Low. Settings/billing are org-level aggregates, org-scoped queries. No mutation of firewall state. However, if monthly_budget_tokens is added and operators want enforcement, gateway must read this value at request time (add to AuthContext.monthly_budget, validate in main.py before allowing request). Design carefully: budget enforcement could become a denial-of-service vector if attacker can PATCH budget to 0 on victim org. Mitigation: PATCH /api/organization/settings requires admin role (enforced in view). Test: confirm non-admin user cannot lower budget."
  },
  {
    "title": "User Invitation and Role Management (missing POST /organization/users (invite), PATCH /organization/users/{user_id} (role update))",
    "severity": "High",
    "detail": "ZeroShield has UserProfile(organization, roles) and auth endpoints (/api/auth/users/) but no invite flow or OpenAI-style role management. Operators cannot programmatically add team members or change their access level. Currently only admins can manually create users via /api/auth/users/.",
    "files": "control/ai_mesh_control/auth/models.py:L33-L66 (UserProfile, Role models); control/ai_mesh_control/auth/views.py (UserManagementListCreateView, UserManagementDetailView); control/ai_mesh_control/auth/urls.py (path('users/', ...))",
    "implementationApproach": "1. Create org_users_views.py (new file) with:\n   - POST /api/organization/users (InviteUserView): accepts {email, role}. Creates User + UserProfile with organization from request.user.org, sends email invite (optional, or just returns invite link). Returns created UserProfile.\n   - GET /api/organization/users (ListUsersView): UserProfile.objects.filter(organization=user.org)\n   - PATCH /api/organization/users/{user_id} (UpdateUserRoleView): updates roles for org members. Permissions: org admin only.\n   - DELETE /api/organization/users/{user_id} (RemoveUserView): soft-remove (set organization=None or is_active=False) or hard-delete. Permissions: org admin only.\n\n2. New serializers (organization_serializers.py):\n   ```python\n   class UserInviteSerializer(Serializer):\n       email = EmailField()\n       role = ChoiceField(choices=['admin', 'analyst', 'viewer'])  # use existing Role choices\n   \n   class UserResponseSerializer(ModelSerializer):\n       class Meta:\n           model = UserProfile\n           fields = ['id', 'user_id', 'display_name', 'roles', 'created_at']\n   ```\n\n3. Integrate with existing auth flow:\n   - Invite sends reset-password link (or just registers with temp password)\n   - New user logs in, resets password, is now org member\n   - Role assignment auto-grants permissions (e.g., 'admin' role can call PATCH /api/organization/settings)\n\n4. Permissions: reuse IsOrgMember (user.profile.organization == obj.organization) and IsOrgAdmin (user.profile.roles contains 'admin' or superuser).\n\n5. **Optional**: add invite token model to track pending invites (Invite(email, organization, role, token, created_at, claimed_at)).\n\n6. **Reuse pipeline**: no firewall changes. Org isolation inherited.",
    "effort": "M",
    "firewallRisk": "Low. User management is org-scoped. However, role/permissions design must be clear: ensure 'admin' role is the ONLY role that can POST/PATCH/DELETE /api/organization/* (not 'analyst' or 'viewer'). Test matrix: admin can invite+revoke, analyst can view, viewer cannot invite. If inconsistent, a viewer-role user could escalate by inviting a new admin account."
  },
  {
    "title": "OpenAI-Compatible Error Responses for Org/Admin Endpoints (missing standard OpenAI error envelope for tenant mgmt)",
    "severity": "Medium",
    "detail": "ZeroShield's /v1/chat/completions returns OpenAI-shaped error bodies (\u00a74 of ZeroShieldResponse.v1.md: {error, message, code}). New org/project/user endpoints must use the same envelope for consistency. Currently no standard defined for tenant-mgmt error responses.",
    "files": "gateway/ai_mesh_gateway/main.py:L401-L450 (_build_safe_block_response, _build_block_response); docs/contracts/ZeroShieldResponse.v1.md (error body specs)",
    "implementationApproach": "1. Define OpenAI-compatible error response in control plane (organization_views.py):\n   ```python\n   def openai_error_response(code: str, message: str, status_code: int):\n       return Response(\n           {\n               'error': code,\n               'message': message,\n               'code': code,  # OpenAI repeats code in both error.error and error.code\n           },\n           status=status_code,\n       )\n   ```\n\n2. Use in views:\n   - 401 Unauthorized: {error: 'unauthorized', message: 'Invalid authentication.', code: 'unauthorized'}\n   - 403 Forbidden (non-admin user tries org mgmt): {error: 'forbidden', message: 'You do not have permission to manage this organization.', code: 'insufficient_permissions'}\n   - 404 Not Found: {error: 'not_found', message: 'Project not found.', code: 'not_found'}\n   - 409 Conflict (project name already used): {error: 'conflict', message: 'A project with this name already exists.', code: 'conflict'}\n\n3. **Firewall Risk**: None (response shaping only). Ensure error messages do not leak sensitive info (e.g., 'user_id=5' in message). Use generic text only.",
    "effort": "S",
    "firewallRisk": "Low. Error responses are read-only (no side effects). Ensure no PII in messages; audit existing error_responses in control/core/views.py for leakage patterns."
  },
  {
    "title": "Project-Scoped Model Restrictions and Usage Analytics (missing per-project model allowlist + telemetry)",
    "severity": "Medium",
    "detail": "ZeroShield's GatewayAPIKey supports per-key allowed_models restrictriction (forwarded to auth context). But no project-level model policy (e.g., all keys in project X can only use gpt-4o). Also no per-project usage/spend tracking exposed via API (operators must query MongoDB telemetry manually).",
    "files": "control/ai_mesh_control/core/models.py:L326-L330 (GatewayAPIKey.allowed_models); gateway/ai_mesh_gateway/middleware.py:L93-L94 (AuthContext.allowed_models); gateway/ai_mesh_gateway/main.py:L5721+ (model validation during chat/completions)",
    "implementationApproach": "1. **Per-Project Model Policy** (optional extension):\n   - Add ProjectModelPolicy model to control/ai_mesh_control/core/models.py:\n     ```python\n     class ProjectModelPolicy(models.Model):\n         project_id = CharField(max_length=128, unique=True)\n         allowed_models = JSONField(default=list)  # [\"gpt-4o\", \"gpt-4o-mini\"]\n         enforcement_mode = CharField(choices=[('allow', 'Allow'), ('block', 'Block')], default='block')\n     ```\n   - On chat/completions request, gateway fetches project policy from control plane (or caches in Redis)\n   - Intersection of key.allowed_models AND project_policy.allowed_models\n\n2. **Project Usage Endpoint** (no-code-change option):\n   - GET /api/organization/projects/{project_id}/usage?period=month returns aggregated data:\n     ```json\n     {\n       \"project_id\": \"proj_001\",\n       \"period\": \"2025-06-01 to 2025-06-30\",\n       \"total_requests\": 5432,\n       \"total_tokens\": 1234567,\n       \"blocked_requests\": 12,\n       \"models_used\": {\"gpt-4o-mini\": 800000, \"claude-3-sonnet\": 434567},\n       \"estimated_cost\": 12.34\n     }\n     ```\n   - Queries EnforcementEvent.objects.filter(agent__endpoint__organization=user.org, project_id=project_id, created_at__gte=start)\n   - Aggregates token counts from gateway telemetry (stored in Agent.metadata or separate TelemetryEvent model)\n\n3. **Reuse firewall pipeline**: org-scoped queries, no data-plane mutation. If project policy model added, gateway must validate (minimal latency impact, cached in Redis).",
    "effort": "M",
    "firewallRisk": "Low-to-Medium. Project policy lookup on hot path (/v1/chat/completions) must be cached (Redis, with TTL=5min). If cache is stale, policy enforcement degrades. Mitigation: implement cache invalidation on project policy update (signal \u2192 flush Redis key). Test: update project policy \u2192 confirm model rejection sub-second on next request."
  },
  {
    "title": "API Key Metadata and Audit Trail (missing GET /organization/api_keys?filter=, POST /organization/audit_log)",
    "severity": "Medium",
    "detail": "OpenAI admin API exposes detailed key metadata (created_by, last_used, usage count) and audit log (who created key X, when it was rotated, when disabled). ZeroShield logs to Django logs + MongoDB telemetry, but no structured audit-log endpoint for operators to query who changed what and when.",
    "files": "control/ai_mesh_control/core/models.py:L281-L375 (GatewayAPIKey has owner, created_at, updated_at, last_used_at but no audit trail FK); control/ai_mesh_control/core/gateway_key_views.py:L331-L342 (logs create event)",
    "implementationApproach": "1. Add AuditLog model (control/ai_mesh_control/core/models.py):\n   ```python\n   class AuditLog(models.Model):\n       organization = ForeignKey(Organization, on_delete=CASCADE, related_name='audit_logs')\n       actor = ForeignKey(User, on_delete=SET_NULL, null=True)\n       action = CharField(choices=[('create_key', 'Create API Key'), ('rotate_key', 'Rotate Key'), ('disable_key', 'Disable Key'), ('delete_key', 'Delete Key'), ('invite_user', 'Invite User'), ('remove_user', 'Remove User'), ('update_project', 'Update Project'), ...])\n       resource_type = CharField(max_length=32)  # 'api_key', 'project', 'user'\n       resource_id = CharField(max_length=255)\n       changes = JSONField(default=dict)  # {'is_active': [True, False]}\n       timestamp = DateTimeField(auto_now_add=True)\n       ip_address = CharField(max_length=45, blank=True)  # for compliance\n   ```\n\n2. Fire audit events on mutations:\n   ```python\n   # In GatewayAPIKeyViewSet.create():\n   AuditLog.objects.create(\n       organization=request.user.profile.organization,\n       actor=request.user,\n       action='create_key',\n       resource_type='api_key',\n       resource_id=str(instance.id),\n       ip_address=get_client_ip(request),\n   )\n   ```\n\n3. Expose via AuditLogView:\n   - GET /api/organization/audit_log?action=create_key&resource_type=api_key&since=2025-06-01\n   - Return paginated AuditLogSerializer(many=True)\n\n4. **Firewall Risk**: None (read-only queries, no enforcement changes). However, ensure audit log queries are org-scoped (filter(organization=user.org)); a user should not see another org's audit trail.",
    "effort": "M",
    "firewallRisk": "Low. Audit log is append-only, org-scoped. No risk to firewall logic. Compliance benefit: operators can prove who revoked a compromised key and when."
  },
  {
    "title": "Bulk Operations and Export (missing POST /organization/export, POST /organization/bulk_actions)",
    "severity": "Low",
    "detail": "OpenAI exposes bulk export (download all keys, users, projects as CSV) and bulk actions (disable all keys for user X, remove all members with role Y). ZeroShield has no bulk operations \u2014 operators must script the API.",
    "files": "control/ai_mesh_control/core/gateway_key_views.py (list-only ViewSet, no bulk action)",
    "implementationApproach": "1. **Export** \u2014 simple GET with format param:\n   ```python\n   class ExportView(APIView):\n       def get(self, request, format='json'):\n           org = request.user.profile.organization\n           if format == 'csv':\n               # Return CSV for keys, users, projects\n               data = export_org_to_csv(org)\n               response = HttpResponse(data, content_type='text/csv')\n           elif format == 'json':\n               data = ExportSerializer(org).data\n               response = Response(data)\n           return response\n   ```\n\n2. **Bulk Actions** \u2014 POST with action + filters:\n   ```python\n   class BulkActionView(APIView):\n       def post(self, request):\n           action = request.data.get('action')  # 'disable_all_keys', 'remove_user_from_all_projects'\n           filters = request.data.get('filters')  # {role: 'viewer'}\n           if action == 'disable_all_keys':\n               GatewayAPIKey.objects.filter(\n                   organization=request.user.profile.organization,\n                   **filters\n               ).update(is_active=False)\n               # Trigger Redis flush for affected keys\n           return Response({'count': count})\n   ```\n\n3. **Firewall Risk**: None. Bulk operations are high-level API convenience (equivalent to N individual DELETE calls). Ensure org isolation on bulk filters.",
    "effort": "S",
    "firewallRisk": "Low. Bulk ops delegate to existing model managers (which are org-scoped). No new enforcement surface."
  }
]