/**
 * Per-submodule "How to Use" content.
 * Each entry drives the HowToUse component rendered on every FirewallModulePage.
 *
 * Schema:
 *   title        – section heading
 *   audience     – short audience tag line
 *   intro        – 1-2 sentence non-technical orientation
 *   steps        – [{id, label, text}]  numbered walkthrough
 *   codeTabs     – [{id, label, language, code}]  copy-able code snippets
 *   notes        – [{icon, text}]  optional callout pills
 */

import {
  resolveBackendBaseUrl,
  resolveFrontendBaseUrl,
  resolveMcpGatewayBaseUrl,
} from "../utils/environmentUrls";

const GATEWAY_BASE_URL = resolveMcpGatewayBaseUrl() || "https://aisecshieldgateway.zeroshield.ai";
const BACKEND_BASE_URL = resolveBackendBaseUrl() || "http://127.0.0.1:8100";
const FRONTEND_BASE_URL = resolveFrontendBaseUrl() || "http://localhost:5173";

const URL_REPLACEMENTS = [
  ["https://aisecshieldgateway.zeroshield.ai", GATEWAY_BASE_URL],
  ["http://127.0.0.1:8300", GATEWAY_BASE_URL],
  ["http://localhost:8300", GATEWAY_BASE_URL],
  ["http://127.0.0.1:8100", BACKEND_BASE_URL],
  ["http://localhost:8100", BACKEND_BASE_URL],
  ["http://localhost:5173", FRONTEND_BASE_URL],
  ["http://127.0.0.1:5173", FRONTEND_BASE_URL],
];

function applyUrlReplacements(text) {
  let nextText = String(text || "");
  URL_REPLACEMENTS.forEach(([from, to]) => {
    if (!from || !to || from === to) return;
    nextText = nextText.split(from).join(to);
  });
  return nextText;
}

function deepReplaceUrls(value) {
  if (typeof value === "string") return applyUrlReplacements(value);
  if (Array.isArray(value)) return value.map(deepReplaceUrls);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([k, v]) => [k, deepReplaceUrls(v)])
    );
  }
  return value;
}

const HOW_TO_USE_RAW = {
  /** ─────────────────────────────────────────────────────── 1.1 ── */
  "1.1": {
    title: "Route Your AI Requests Through the Gateway",
    audience: "Works for technical & non-technical users",
    intro:
      "Instead of calling OpenAI, Anthropic, or any other AI provider directly, point your application at the AISecShield Gateway. Your app keeps the same general request pattern, but responses can be allowed, blocked, redacted, or rerouted when security or policy rules apply.",
    steps: [
      {
        id: "how-mesh-protects",
        label: "How AI Mesh Firewall Protects Requests",
        text: "The Firewall acts as a transparent reverse proxy. All API requests flow through it where they are scanned in real-time. It enforces authentication, rate limiting, and checks for malicious inputs before forwarding clean traffic to your AI models.",
      },
      {
        id: "how-block-rewrite",
        label: "Why Responses May be Blocked or Rewritten",
        text: "Guardrails analyze both prompts and model output inline. Dangerous requests are blocked immediately, sensitive output can be redacted before release, and uncertain or hallucination-prone answers can be flagged into human review instead of being delivered silently.",
      },
      {
        id: "how-routing-decisions",
        label: "How Routing Decisions Occur",
        text: "Every `/v1/chat/completions` request is evaluated by the AI Mesh Router. The gateway first applies your weights and policy filters, then an deterministic routing engine confirms or overrides the preferred model based on sensitivity, compliance, budget, latency, and model risk. The final route and reason are returned in ZeroShield metadata and the routing audit trail.",
      },
      {
        id: "how-rag-security",
        label: "How RAG Security Works",
        text: "RAG security restricts which collections and namespaces each tenant can query, scans retrieved chunks before assembly, and returns an explicit deny when a user attempts cross-tenant access or unsafe context expansion.",
      },
      {
        id: "how-org-security-boundary",
        label: "How Organisation Security Works",
        text: "Organisation is the root security boundary. Users in the same organisation share only organisation-scoped dashboard state, logs, telemetry, vector resources, MCP configurations, device inventory, and agent bundles. Users outside the organisation cannot view, query, or infer those resources. Admin-managed provider keys are reused safely only inside that same organisation scope.",
      },
      {
        id: "step-key",
        label: "Get your Gateway API Key",
        text: 'Open the "Gateway Keys" control panel above, click "+ New Key", copy the key shown. This key acts as your Authorization token.',
      },
      {
        id: "step-url",
        label: "Change the URL in your app",
        text: 'Replace your existing provider URL (e.g. https://api.openai.com/v1) with the gateway base URL: https://aisecshieldgateway.zeroshield.ai/v1',
      },
      {
        id: "step-auth",
        label: "Use the gateway key as the Bearer token",
        text: "Pass your gateway API key in the Authorization header instead of your OpenAI/Anthropic key. The gateway forwards the request to your configured provider securely.",
      },
      {
        id: "step-done",
        label: "Verify the integration behavior",
        text: "Every request now flows through the AI Mesh Firewall. After switching the base URL and bearer token, run a few real prompts to confirm allowed, blocked, redacted, and flagged-for-review outcomes match your policies and appear in the audit trail.",
      },
    ],
    codeTabs: [
      {
        id: "curl",
        label: "cURL",
        language: "bash",
        code: `curl -X POST https://aisecshieldgateway.zeroshield.ai/v1/chat/completions \\
  -H "Authorization: Bearer <your_gateway_api_key>" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "gpt-5.2",
    "messages": [{"role": "user", "content": "What is the capital of France?"}],
    "stream": false
  }'`,
      },
      {
        id: "python-sdk",
        label: "Python (OpenAI SDK)",
        language: "python",
        code: `from openai import OpenAI

client = OpenAI(
    base_url="https://aisecshieldgateway.zeroshield.ai/v1",
    api_key="<your_gateway_api_key>",   # gateway key, NOT your OpenAI key
)

response = client.chat.completions.create(
    model="gpt-5.2",
    messages=[{"role": "user", "content": "What is the capital of France?"}],
)

print(response.choices[0].message.content)`,
      },
      {
        id: "javascript",
        label: "JavaScript (fetch)",
        language: "javascript",
        code: `const response = await fetch(
  "https://aisecshieldgateway.zeroshield.ai/v1/chat/completions",
  {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: "Bearer <your_gateway_api_key>",
    },
    body: JSON.stringify({
      model: "gpt-5.2",
      messages: [{ role: "user", content: "What is the capital of France?" }],
    }),
  }
);

const data = await response.json();
console.log(data.choices[0].message.content);`,
      },
      {
        id: "langchain",
        label: "LangChain",
        language: "python",
        code: `from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    model="gpt-5.2",
    openai_api_base="https://aisecshieldgateway.zeroshield.ai/v1",
    openai_api_key="<your_gateway_api_key>",
)

response = llm.invoke("What is the capital of France?")
print(response.content)`,
      },
    ],
    notes: [
      {
        icon: "shield",
        text: "Your app keeps the same high-level client flow, but gateway policy can change the result when a request is risky or non-compliant.",
      },
      {
        icon: "shield",
        text: "When operators switch organisation context, data views refresh against org-scoped APIs so prior-tenant logs and metrics are not reused.",
      },
      {
        icon: "info",
        text: "Most OpenAI-compatible clients work with a base_url and api_key change, but some integrations also need model allowlist, auth, or retry behavior validation.",
      },
    ],
  },

  /** ─────────────────────────────────────────────────────── 1.2 ── */
  "1.2": {
    title: "Manage All Policy Families",
    audience: "For operators governing pipeline, RAG, PCM/MCP, and vector enforcement policies",
    intro:
      "Use this page as the unified policy console for the entire AI firewall. Manage pipeline rules, RAG protections, PCM or MCP controls, and vector database access policies from one workspace.",
    steps: [
      {
        id: "step-policy",
        label: "Work section by section",
        text: 'Use the dedicated sections for Pipeline, RAG, PCM / MCP, Vector, and Analytics so each policy family stays organized and reviewable on one page.',
      },
      {
        id: "step-vector",
        label: "Manage vector access policies",
        text: 'Use the "Vector DB Policies" section to define project, collection, and namespace isolation rules. This is where you control allowed operations, result caps, anomaly thresholds, and sensitive document handling.',
      },
      {
        id: "step-priority",
        label: "Create, edit, and refine rules",
        text: 'Within each generic policy section, open a policy to create, edit, or remove rules. Tune each rule action to block, redact, or monitor, and use priorities to control evaluation order.',
      },
      {
        id: "step-review",
        label: "Review enforcement evidence",
        text: "Use the policy analytics and evidence sections on this page to confirm which rule families are active and whether changes reduce risky behavior across the stack.",
      },
    ],
    codeTabs: [
      {
        id: "rag-policy-json",
        label: "RAG Policy JSON",
        language: "json",
        code: `{
  "name": "Block Prompt Injection In Retrieval",
  "code": "RAG_PROMPT_INJECTION",
  "category": "Retrieval Security",
  "severity": "HIGH",
  "enabled": true,
  "priority": 50,
  "description": "Blocks prompt injection patterns discovered in retrieved chunks before they reach the generator."
}`,
      },
      {
        id: "vector-policy-json",
        label: "Vector Policy JSON",
        language: "json",
        code: `{
  "name": "Customer KB Namespace Isolation",
  "project_id": "proj-customer-success",
  "collection_name": "customer_kb",
  "vector_db_type": "pinecone",
  "namespace": "tenant-a",
  "default_action": "deny",
  "allowed_operations": ["query"],
  "max_results_per_query": 5,
  "require_context_scan": true,
  "block_sensitive_documents": true,
  "anomaly_distance_threshold": 0.85
}`,
      },
      {
        id: "policy-api",
        label: "Policy API",
        language: "bash",
        code: `# List current retrieval policies
curl https://aisecshieldgateway.zeroshield.ai/api/policies/ \
  -H "Authorization: Bearer <your_gateway_api_key>"

# Create a new vector isolation policy
curl -X POST https://aisecshieldgateway.zeroshield.ai/api/vector-policies/ \
  -H "Authorization: Bearer <your_gateway_api_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Tenant A Isolation",
    "project_id": "proj-customer-success",
    "collection_name": "customer_kb",
    "vector_db_type": "pinecone",
    "namespace": "tenant-a",
    "default_action": "deny",
    "allowed_operations": ["query"]
  }'`,
      },
    ],
    notes: [
      {
        icon: "shield",
        text: "Keep pipeline, RAG, and vector policies aligned: blocking risky prompts without namespace isolation or domain-specific controls still leaves room for data leakage.",
      },
      {
        icon: "info",
        text: "This page is for policy authoring and governance. Use the combined RAG & Vector DB page to run simulators and operational retrieval tests.",
      },
    ],
  },

  /** ─────────────────────────────────────────────────────── 1.3 ── */
  "1.3": {
    title: "Operate Your RAG & Vector DB Firewall",
    audience: "For engineers securing retrieval pipelines, managing vector providers, and governing document ingestion",
    intro:
      "The RAG firewall is guardrails-only: you own the vector DB, embedding model, ranking, and generation — we secure the data on the way in and out. Use this page to connect your vector DB provider (with your BYOK embedding model), manage collections, ingest documents (tier-1 + tier-2 scan → optional typed-placeholder redaction → BYOK embed → store), and run semantic search. Every ingest and query call passes through the AI Mesh Firewall, where namespace isolation and policy enforcement are applied before data is stored or returned.",
    steps: [
      {
        id: "step-provider",
        label: "1. Configure your vector DB provider",
        text: 'Open the "Vector Provider Configuration" panel. Select a provider (Pinecone, Milvus, or a custom Milvus-compatible endpoint), enter the connection URL, API key, and your BYOK embedding model, then click Save. The key is stored securely and never exposed in the UI — only a "Key Set" badge appears. These org-level credentials are used automatically for all gateway operations.',
      },
      {
        id: "step-connect",
        label: "2. Verify database connectivity",
        text: 'Open "Database Connection" to confirm the gateway can reach your vector store. The status indicator shows green when connected, amber when degraded, and red when disconnected.',
      },
      {
        id: "step-collections",
        label: "3. Create and manage collections",
        text: 'Use the "Collection Manager" panel to create new collections (indexes). Select a provider, enter a collection name, and click Create. All existing collections are listed with delete capability. The gateway enforces namespace isolation automatically.',
      },
      {
        id: "step-pipeline",
        label: "4. Configure RAG guardrails",
        text: 'In the firewall settings, toggle Document Redaction (typed-placeholder PII redaction before embedding) and Tier-2 Scanning (the ML guard model on ingest + query) for RAG. By default the query path is guardrails-only — query and retrieved-document scanning form the chain-of-custody audit trail; ranking and generation stay in your own pipeline.',
      },
      {
        id: "step-ingest",
        label: "5. Ingest documents (single or bulk)",
        text: 'Use "RAG Ingestion" in single mode to paste content directly, or switch to bulk mode to upload .txt, .md, .csv, or .json files. Each document is scanned (tier-1 + optional tier-2) for poisoning, PII, and malware payloads, then optionally PII-redacted with typed placeholders, then embedded with your BYOK model before entering your vector store. Set sensitivity levels (unclassified, confidential, restricted) per ingestion.',
      },
      {
        id: "step-search",
        label: "6. Run semantic search",
        text: 'Use the "Semantic Search" panel to query your collections. Select a provider and collection, type your query, and set the number of results. The gateway scans the query and the retrieved documents, then returns the retriever-approved results — ranking and generation stay in your own pipeline. Results show document content, distance scores, scan verdicts, policy action badges, and the pipeline trace. Cross-tenant collection names are denied explicitly instead of falling back silently.',
      },
      {
        id: "step-simulate",
        label: "7. Test attack scenarios",
        text: 'Use the "RAG Feature Test" and "Attack & Trust Simulator" panels together to validate that prompt injection, namespace bypass, poisoning, and exfiltration attacks are properly blocked by your policies.',
      },
      {
        id: "step-monitor",
        label: "8. Review telemetry and evidence",
        text: 'Use the telemetry section to confirm where retrieval pressure builds and whether combined RAG/vector protections block risky requests. Check the policy action badges (ALLOW / BLOCK / MONITOR) on search results.',
      },
      {
        id: "step-automated-validation",
        label: "9. Run automated prompt security validation",
        text: 'Use the built-in security harness to run PromptFoo, Garak, and the live Module 1 proof checks in one command. The report tells non-technical operators what passed, what was blocked, and whether rate limits, kill-switches, routing, vector access, and MCP controls still work after changes.',
      },
    ],
    codeTabs: [
      {
        id: "provider-config",
        label: "Provider Config API",
        language: "bash",
        code: `# Configure an org-level vector provider (admin)
curl -X POST http://localhost:8100/api/vector-providers/ \\
  -H "Authorization: Bearer <jwt_token>" \\
  -H "Content-Type: application/json" \\
  -d '{
    "provider_type": "pinecone",
    "display_name": "Production Pinecone",
    "connection_url": "https://us-east1-gcp.pinecone.io",
    "api_key": "pcsk_...",
    "environment": "us-east1-gcp",
    "embedding_model": "text-embedding-3-small",
    "is_active": true
  }'`,
      },
      {
        id: "collection-create",
        label: "Create Collection",
        language: "bash",
        code: `# Create a new vector collection via the gateway
curl -X POST http://localhost:8300/v1/rag/collections \\
  -H "Authorization: Bearer <gateway_api_key>" \\
  -H "Content-Type: application/json" \\
  -d '{
    "collection_name": "customer-kb",
    "vector_db_type": "pinecone"
  }'`,
      },
      {
        id: "secure-ingest",
        label: "Secure Ingestion",
        language: "python",
        code: `import requests

# Single document ingestion
payload = {
    "collection": "customer-kb",
    "content": "Quarterly renewal FAQ content...",
    "metadata": {
        "source": "handbook",
        "namespace": "tenant-a",
        "sensitivity": "confidential"
    }
}
resp = requests.post(
    "http://localhost:8300/v1/rag/ingest",
    headers={"Authorization": "Bearer <gateway_key>"},
    json=payload, timeout=30,
)
print(resp.json())

# Bulk ingestion
bulk = {
    "collection": "customer-kb",
    "documents": [
        {"content": "Doc 1...", "metadata": {"source": "file1.txt"}},
        {"content": "Doc 2...", "metadata": {"source": "file2.txt"}},
    ],
    "metadata": {"namespace": "tenant-a"}
}
resp = requests.post(
    "http://localhost:8300/v1/rag/ingest",
    headers={"Authorization": "Bearer <gateway_key>"},
    json=bulk, timeout=60,
)`,
      },
      {
        id: "semantic-search",
        label: "Semantic Search",
        language: "bash",
        code: `# Guardrails-only query: scan + retrieve through your vector DB.
# Ranking and generation stay in your own pipeline.
curl -X POST http://localhost:8300/v1/rag/query \\
  -H "Authorization: Bearer <gateway_api_key>" \\
  -H "Content-Type: application/json" \\
  -d '{
    "query": "What are the latest renewal terms?",
    "collection": "customer-kb",
    "n_results": 5,
    "vector_db_type": "pinecone",
    "namespace": "tenant-a"
  }'`,
      },
      {
        id: "langchain-rag",
        label: "LangChain RAG",
        language: "python",
        code: `from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_community.vectorstores import Pinecone

embeddings = OpenAIEmbeddings(
    openai_api_base="https://aisecshieldgateway.zeroshield.ai/v1",
    openai_api_key="<your_gateway_api_key>",
)
llm = ChatOpenAI(
    openai_api_base="https://aisecshieldgateway.zeroshield.ai/v1",
    openai_api_key="<your_gateway_api_key>",
    model="gpt-5.2",
)

vector_store = Pinecone.from_existing_index(
    index_name="customer-kb",
    embedding=embeddings,
)

retriever = vector_store.as_retriever(search_kwargs={"k": 5})
docs = retriever.invoke("What are the latest renewal terms?")
answer = llm.invoke(f"Answer using these docs: {docs}")
print(answer.content)`,
      },
      {
        id: "automated-security-validation",
        label: "Automated Security Validation",
        language: "bash",
        code: `# Run full adversarial validation harness (PromptFoo + Garak + live Module 1 checks)
bash scripts/run_security_validation.sh

      # Inspect the single proof artifact and the detailed live report
cat tests/security/reports/security-summary.json
      cat tests/security/reports/module1-live-validation.json
cat tests/security/reports/security-summary.md

# Run only PromptFoo regression checks
npx promptfoo@latest eval \
  -c tests/security/promptfoo/promptfooconfig.chat.yaml \
  --output tests/security/reports/promptfoo-chat-results.json \
  --no-cache

# Run only Garak gateway probe suite
bash scripts/run_garak_gateway.sh`,
      },
    ],
    notes: [
      {
        icon: "shield",
        text: "Provider API keys are stored securely with write-only access. The UI never displays the actual key — only a confirmation that one is set. Rotate keys through the Provider Configuration panel.",
      },
      {
        icon: "warning",
        text: "Re-run ingestion and search tests after every policy change to confirm retrieval behavior still matches expectations. Check the pipeline trace on search results for stage-by-stage enforcement.",
      },
      {
        icon: "info",
        text: "The credential hierarchy is: per-request → org-level provider config → environment defaults. Org configs override env vars so admins can rotate keys without redeploying.",
      },
      {
        icon: "shield",
        text: "Validation artifacts are written to tests/security/reports and can be uploaded in CI. Use security-summary.json for the overall verdict and module1-live-validation.json when you need step-by-step operator evidence.",
      },
    ],
  },

  /** ─────────────────────────────────────────────────────── 1.4 ── */
  "1.4": {
    title: "Operate MCP Guardrails & Audit",
    audience: "For teams using AI agents with tool use (MCP servers)",
    intro:
      "The MCP manager controls MCP server onboarding, tool gating, centralized MCP-domain policies, structured activity events, and incident visibility. All runtime decisions are shown through threat-feed and audit records, not raw backend log streams.",
    steps: [
      {
        id: "step-add",
        label: 'Click "Add Server" and connect',
        text: 'Add a server with Streamable HTTP, SSE, or stdio transport. For OAuth-capable servers, the connect action opens the authorization flow automatically when required.',
      },
      {
        id: "step-tools",
        label: "Review tool inventory and risk analysis",
        text: "Use the Tools tab to inspect discovered permissions, risk category, risk score, and compliance tags before enabling access for agents.",
      },
      {
        id: "step-policy",
        label: "Create MCP-domain policies",
        text: "Create policies in the Policies tab (stored in the central Policy Management domain with policy_domain=mcp). Include tool scope, action, blocked params, redaction fields, and token limits in metadata.",
      },
      {
        id: "step-activity",
        label: "Monitor Activity & Incidents",
        text: "Use Activity & Incidents to track MCP threat-feed events and incident state changes (open, escalated, resolved) for operator response.",
      },
      {
        id: "step-audit",
        label: "Inspect structured audit records",
        text: "Use Audit Log for per-action records including actor, organization, input/output summaries, policy decision, duration, errors, and compliance tags.",
      },
    ],
    codeTabs: [
      {
        id: "http-mcp",
        label: "Streamable HTTP / SSE",
        language: "bash",
        code: `# Create an MCP server via Django API
curl -X POST http://127.0.0.1:8300/api/mcp/servers/ \\
  -H "Content-Type: application/json" \\
  -H "Authorization: Bearer <JWT>" \\
  -d '{
    "name": "linear-gateway",
    "transport": "streamable_http",
    "url": "https://gateway.example.com/mcp/linear"
  }'

# Connect and discover tools
curl -X POST http://127.0.0.1:8300/api/mcp/servers/<server_id>/connect/ \\
  -H "Content-Type: application/json" \\
  -H "Authorization: Bearer <JWT>" \\
  -d '{"redirect_uri":"http://localhost:5173/oauth/callback"}'`,
      },
      {
        id: "mcp-policy",
        label: "MCP Policy (API)",
        language: "bash",
        code: `# Create an MCP tool policy via the backend API
curl -X POST http://127.0.0.1:8300/api/mcp/policies/ \\
  -H "Content-Type: application/json" \\
  -H "Authorization: Bearer <JWT>" \\
  -d '{
    "name": "Block destructive DB tools",
    "server": "<server_uuid>",
    "action": "deny",
    "priority": 80,
    "is_active": true,
    "blocked_parameters": {"target": ["*"], "confirm": ["*"]},
    "redaction_fields": []
  }'

# List policies for all servers in your org
curl -X GET http://127.0.0.1:8300/api/mcp/policies/ \\
  -H "Authorization: Bearer <JWT>"

# List policies for a specific server
curl -X GET 'http://127.0.0.1:8300/api/mcp/policies/?server_id=<server_uuid>' \\
  -H "Authorization: Bearer <JWT>"`,
      },
      {
        id: "custom-mcp",
        label: "Custom Stdio MCP",
        language: "bash",
        code: `# Register a custom stdio MCP server
curl -X POST http://127.0.0.1:8300/api/mcp/servers/ \\
  -H "Content-Type: application/json" \\
  -H "Authorization: Bearer <JWT>" \\
  -d '{
    "name": "filesystem-tools",
    "transport": "stdio",
    "command": "npx",
    "args": ["-y", "@anthropic/mcp-server-filesystem", "/home/user/docs"]
  }'

# Connect to discover tools and risk scores
curl -X POST http://127.0.0.1:8300/api/mcp/servers/<server_id>/connect/ \\
  -H "Authorization: Bearer <JWT>"`,
      },
      {
        id: "mcp-other",
        label: "VS Code / Cursor / Claude",
        language: "json",
        code: `// ZeroShield supports native MCP OAuth — no manual JSON editing needed!
//
// ➊ Cmd+Shift+P → "MCP: Add Server" → choose "HTTP (Streamable)"
// ➋ Paste the gateway URL (click "Copy Endpoint" next to any server)
// ➌ VS Code opens a browser → enter your Gateway API Key → done!
//
// VS Code automatically handles auth via OAuth 2.1 and connects.
// The mcp.json entry is created for you:

"zeroshield-my-server": {
  "url": "http://<gateway_host>:<gateway_port>/gateway/<org_slug>/mcp/<server_slug>",
  "type": "http"
}

// That's it — no headers, no inputs, no API key in the config file.
// The OAuth token is stored securely by VS Code's credential manager.`,
      },
    ],
    notes: [
      {
        icon: "warning",
        text: "Use Activity & Incidents and Audit Log for MCP visibility. Raw backend/service log streaming is intentionally not shown in this module.",
      },
      {
        icon: "info",
        text: "Linear's MCP server requires OAuth authentication via the mcp-remote OAuth flow. Complete auth in the browser popup that appears when first connecting.",
      },
      {
        icon: "shield",
        text: "Tools with exec, filesystem, or credential permissions are automatically flagged for review regardless of risk score.",
      },
    ],
  },

  /** ─────────────────────────────────────────────────────── 1.5 ── */
  "1.5": {
    title: "Configure Multi-Model Routing & Governance",
    audience: "For teams running multiple AI providers or models",
    intro:
      "The AI Mesh Router automatically evaluates every `/v1/chat/completions` request. Your configured weights and sensitivity rules create the candidate pool, then an deterministic routing engine analyzes the input and governance settings to decide whether to keep the requested model or reroute to a safer, cheaper, faster, or more compliant target. The full routing decision — including score, reason, policy summary, and decision factors — is returned in the API response and visible in the Routing Audit panel.",
    steps: [
      {
        id: "step-keys",
        label: "Add your model provider API keys",
        text: 'Open "Model Connection" above. Add keys for OpenAI, Anthropic, AWS Bedrock, Azure OpenAI, and any other provider. Keys you paste are stored encrypted server-side. A model can also be connected via an environment-variable reference managed on the gateway, in which case the key is never stored in ZeroShield.',
      },
      {
        id: "step-rules",
        label: "Configure routing governance weights",
        text: 'Open the Routing Governance panel to set weights for Risk, Cost, Latency, and Priority (they are normalized to 100% by the gateway (the panel shows the raw sum)). Choose a data sensitivity level (Public, Internal, Confidential, Restricted) and optionally select a strategy preset like "Balanced", "Cost Optimized", "Low Latency", "Maximum Security", or "Quality First". These settings flow directly into the deterministic routing engine for every chat-completion request.',
      },
      {
        id: "step-toggle",
        label: "Enable or disable dynamic routing",
        text: 'Use the Dynamic Routing toggle in Module 1.5 to globally enable or disable deterministic routing for `/v1/chat/completions`. When disabled, requests keep the preferred model unless blocked by allowlist or policy constraints.',
      },
      {
        id: "step-point",
        label: "Point your application at the gateway",
        text: "Change your base_url to the gateway URL. Your app can still send a preferred model, but `/v1/chat/completions` treats it as a routing hint rather than a hard guarantee. The final model and reroute reason are visible in the `zeroshield.routing` object in the API response and in the Routing Audit panel.",
      },
      {
        id: "step-response",
        label: "Read the routing metadata in the response",
        text: 'Every API response includes a `zeroshield.routing` object with: `routed_model` (the model actually used), `decision_source` ("deterministic_weighted"), `routing_score`, `routing_reason`, `policy_summary`, `decision_factors` (scoring breakdown), `fallback_chain`, `data_sensitivity`, `compliance_requirements`, and `weights`. Use this to understand exactly why a model was chosen.',
      },
      {
        id: "step-audit",
        label: "Monitor routing decisions in the Audit panel",
        text: 'Open the Routing Audit panel to see a live stream of routing events. Each event shows the original→routed model flow, the decision source badge (Deterministic weighted routing), score, sensitivity level, and expandable details with the full reason and weight breakdown.',
      },
      {
        id: "step-failover",
        label: "Failover is automatic",
        text: "If a provider is down, throttled, too risky, over budget, or outside compliance rules, the router falls back to the next eligible model. The `fallback_chain` in the response shows the backup order. Operators can see the original model, final routed model, decision source, and fallback chain in real time.",
      },
    ],
    codeTabs: [
      {
        id: "auto-route",
        label: "Auto-routing (any SDK)",
        language: "python",
        code: `from openai import OpenAI

# Same code works regardless of which model the router selects
client = OpenAI(
    base_url="https://aisecshieldgateway.zeroshield.ai/v1",
    api_key="<your_gateway_api_key>",
)

# The deterministic routing engine evaluates every request
# and selects the optimal model based on content analysis + governance settings
response = client.chat.completions.create(
    model="gpt-4",  # preferred model — treated as a routing hint
    messages=[{"role": "user", "content": "Summarize this legal document..."}],
    extra_body={
        "routing_preferences": {
          "enable_routing": true,
            "compliance_requirements": ["HIPAA"],
            "data_sensitivity": "confidential",
            "latency_budget_ms": 1500,
        }
    },
)

# The routing decision is in the zeroshield metadata:
routing = response.zeroshield["routing"]
print(f"Routed to: {routing['routed_model']}")
print(f"Decision: {routing['decision_source']}")
print(f"Reason:   {routing['routing_reason']}")
print(f"Score:    {routing['routing_score']}")
print(f"Factors:  {routing['decision_factors']}")
print(f"Fallback: {routing['fallback_chain']}")`,
      },
      {
        id: "response-format",
        label: "Response Metadata",
        language: "json",
        code: `{
  "zeroshield": {
    "routing": {
      "original_model": "gpt-4",
      "routed_model": "claude-opus-4.6",
      "routed_model_id": "anthropic/claude-opus-4-6",
      "routing_score": 0.9802,
      "routing_enabled": true,
      "routing_override": null,
      "decision_source": "deterministic_weighted",
      "routing_reason": "Routing policy selected 'claude-opus-4.6' (score=0.9802) from 14 candidates. Weights: risk=20%, cost=10%, latency=10%, priority=60%.",
      "policy_summary": "Model meets sensitivity=restricted, compliance=['SOC2', 'ISO27001', 'HIPAA', 'GDPR']. Risk=0.07, latency_sla=6000ms.",
      "decision_factors": [
        "model_score=0.9802",
        "risk_component=0.9300",
        "cost_component=0.9420",
        "latency_component=1.0000",
        "priority_component=1.0000",
        "candidates_evaluated=14",
        "data_sensitivity=public"
      ],
      "candidate_count": 14,
      "fallback_chain": ["gpt-5.2", "claude-sonnet-4", "gemini-3.1-pro"],
      "data_sensitivity": "public",
      "compliance_requirements": [],
      "weights": { "risk": 0.2, "cost": 0.1, "latency": 0.1, "priority": 0.6 }
    }
  }
}`,
      },
      {
        id: "explicit-model",
        label: "Explicit model with fallback",
        language: "python",
        code: `from openai import OpenAI

client = OpenAI(
    base_url="https://aisecshieldgateway.zeroshield.ai/v1",
    api_key="<your_gateway_api_key>",
)

# Request a specific model — gateway may still reroute it if
# ZeroShield deterministic routing finds a better candidate for your constraints
response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hello!"}],
    extra_body={
        "routing_preferences": {
          "enable_routing": false,
            "latency_budget_ms": 800,
            "compliance_requirements": ["SOC2"],
        },
    },
)`,
      },
      {
        id: "zeroshield-routing",
        label: "ZeroShield guard model via gateway",
        language: "python",
        code: `from openai import OpenAI

# Access ZeroShield models through the OpenAI-compatible gateway
client = OpenAI(
    base_url="https://aisecshieldgateway.zeroshield.ai/v1",
    api_key="<your_gateway_api_key>",
)

response = client.chat.completions.create(
    model="zeroshield-model",
    messages=[{"role": "user", "content": "Hello from ZeroShield!"}],
)
print(response.choices[0].message.content)`,
      },
    ],
    notes: [
      {
        icon: "info",
        text: "When Dynamic Routing is enabled, `/v1/chat/completions` requests go through the deterministic routing engine. The routing engine evaluates governance settings and request attributes (weights, sensitivity, compliance) to select the optimal model in real-time.",
      },
      {
        icon: "shield",
        text: "Every routing decision includes full transparency: the `decision_source`, `routing_reason`, `policy_summary`, and `decision_factors` are returned in the API response and shown in the Routing Audit panel.",
      },
      {
        icon: "info",
        text: "Governance settings from the Routing Governance panel (toggle, weights, sensitivity, presets) are applied to every request. Set `routing_preferences.enable_routing: false` in `extra_body` to pin `model` exactly; omit it (or set `true`) to use org dynamic routing.",
      },
    ],
  },

  /** ─────────────────────────────────────────────────────── 1.6 ── */
  "1.6": {
    title: "Configure Kill-Switch & Circuit Breaker Thresholds",
    audience: "For security operators and incident responders",
    intro:
      "The Kill-Switch module watches every active model connection in real time. When a model exceeds the risk threshold — or when an incident is declared — traffic is cut instantly without any application changes required.",
    steps: [
      {
        id: "step-threshold",
        label: "Set your risk score threshold",
        text: 'Open "Kill Switch" panel above. Set the risk score threshold (default: 80 out of 100). When any model\'s rolling risk score exceeds this value, the kill-switch fires automatically.',
      },
      {
        id: "step-actions",
        label: "Choose the isolation action",
        text: 'Select what happens when the threshold fires: "Block traffic to this model only", "Route to backup model", or "Alert only (no block)". Critical incidents can trigger full isolation.',
      },
      {
        id: "step-simulate",
        label: "Test with the Circuit Breaker Simulator",
        text: 'Use "Circuit Breaker Simulator" to replay an incident scenario and verify the kill-switch fires correctly before a real incident occurs.',
      },
      {
        id: "step-manual",
        label: "Manual isolation for active incidents",
        text: 'Click "Isolate Model" in the model status table to immediately stop all traffic to a specific model. The circuit breaker auto-recovers when risk drops below 50% of the threshold.',
      },
      {
        id: "step-audit",
        label: "Review isolation events in the audit trail",
        text: "Every kill-switch event is logged with timestamp, model ID, risk score, triggering request, and recovery time. Review these in the Evidence section below.",
      },
    ],
    codeTabs: [
      {
        id: "rest-isolate",
        label: "Live chat (gateway)",
        language: "bash",
        code: `# Use a gateway API key from THIS org (see Gateway Keys panel).
# "model" must be the registered model_name — not the LiteLLM model_id.
curl -X POST http://127.0.0.1:8180/v1/chat/completions \\
  -H "Authorization: Bearer <your_org_gateway_api_key>" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "live-triage-openai",
    "messages": [{"role": "user", "content": "What is the capital of France?"}],
    "stream": false
  }'

# Sync ModelState rows from Model Connections (control plane)
curl -X POST http://127.0.0.1:8180/api/models/sync/ \\
  -H "Authorization: Bearer <session_or_token>" \\
  -H "Content-Type: application/json"`,
      },
      {
        id: "webhook",
        label: "Receive kill-switch webhooks",
        language: "python",
        code: `# Set up a webhook to be notified when the kill-switch fires
# POST https://aisecshieldgateway.zeroshield.ai/api/config/webhooks

import hmac, hashlib, json
from flask import Flask, request

app = Flask(__name__)
WEBHOOK_SECRET = "<your_webhook_secret>"

@app.route("/zeroshield-webhook", methods=["POST"])
def handle_kill_switch():
    # Verify the signature
    sig = request.headers.get("X-ZeroShield-Signature", "")
    body = request.get_data()
    expected = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return "Forbidden", 403

    event = request.json
    if event["type"] == "kill_switch_fired":
        model_id = event["model_id"]
        risk_score = event["risk_score"]
        print(f"ALERT: Model {model_id} isolated (risk score: {risk_score})")
        # Trigger your incident response workflow here

    return "OK", 200`,
      },
    ],
    notes: [
      {
        icon: "warning",
        text: "Kill-switch isolation is immediate and affects all traffic to the model — no application restart or deployment is required.",
      },
      {
        icon: "info",
        text: "The circuit breaker auto-recovers after the cool-down period. Set an appropriate threshold to avoid false-positive isolations in high-traffic environments.",
      },
    ],
  },

  /** ─────────────────────────────────────────────────────── 1.7 ── */
  "1.7": {
    title: "Configure Output Scanning & Review Queues",
    audience: "For security and compliance teams",
    intro:
      "Every response generated through /v1/chat/completions is evaluated in-line before it reaches the client. Input checks, model generation, and output enforcement now happen in one lifecycle so the UI, review queue, and incident records all reflect the final delivered outcome.",
    steps: [
      {
        id: "step-policies",
        label: "Set output-stage policy actions",
        text: 'Open the response policy panel above and define what should happen when generated content matches a rule. Use block for never-ship content, redact for masking sensitive values, rewrite for safe substitution, and flag when a human should review the response before release.',
      },
      {
        id: "step-pii",
        label: "Verify built-in output scanning",
        text: "Built-in output checks run after generation and before delivery. PII is redacted in place, unsafe or policy-matching content can be rewritten or blocked, and factuality-style findings can be flagged for review without forcing a hard block.",
      },
      {
        id: "step-review",
        label: "Use the Review Queue for flag actions",
        text: "Responses with a final action of flag appear in the Human Review Queue together with the enforcement metadata returned by the gateway. Reviewers can inspect the matched rule set, delivered response, and policy reasoning before approving or rejecting the result.",
      },
      {
        id: "step-incident",
        label: "Track block actions as security incidents",
        text: 'When the final output action is block, the gateway emits an enforcement event and the backend creates a Security Incident automatically. Use the incident table to triage blocked generations, escalate them to responders, and confirm resolution once the policy issue is addressed.',
      },
    ],
    codeTabs: [
      {
        id: "transparent",
        label: "Transparent (no code change)",
        language: "bash",
        code: `# Output enforcement is already inline on /v1/chat/completions.
# No second API call is required.

curl https://aisecshieldgateway.zeroshield.ai/v1/chat/completions \\
  -H "Authorization: Bearer <your_gateway_api_key>" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "gpt-3.5-turbo",
    "messages": [
      {"role": "user", "content": "Summarize this record without exposing personal data."}
    ]
  }'

# Inspect the zeroshield object in the response for:
# - action: allow | flag | rewrite | redact | block
# - rewritten_response or redacted_response
# - review_required
# - security_incident
# - factuality_warning`,
      },
      {
        id: "manual-scan",
        label: "Policy-driven output actions",
        language: "bash",
        code: `# Example response metadata returned from /v1/chat/completions
curl https://aisecshieldgateway.zeroshield.ai/v1/chat/completions \\
  -H "Authorization: Bearer <your_gateway_api_key>" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "gpt-3.5-turbo",
    "messages": [{"role": "user", "content": "Return the phrase silver river signal."}]
  }'

# Sample response excerpt:
# {
#   "choices": [{"message": {"content": "[REDACTED BY POLICY]."}}],
#   "zeroshield": {
#     "action": "redact",
#     "redacted_response": "[REDACTED BY POLICY].",
#     "matched_patterns": ["Dev Output Redact Rule"],
#     "review_required": false,
#     "security_incident": false
#   }
# }`,
      },
      {
        id: "python-guard",
        label: "Python client handling",
        language: "python",
        code: `import httpx

GATEWAY = "https://aisecshieldgateway.zeroshield.ai"
KEY = "<your_gateway_api_key>"

async def safe_complete(prompt: str) -> str:
    """Return the client-visible response after inline output enforcement."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{GATEWAY}/v1/chat/completions",
            headers={"Authorization": f"Bearer {KEY}"},
            json={
                "model": "gpt-3.5-turbo",
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        data = resp.json()
        zeroshield = data.get("zeroshield", {})

        if zeroshield.get("action") == "block":
            raise RuntimeError("Response blocked by output policy")

        return data["choices"][0]["message"]["content"]`,
      },
    ],
    notes: [
      {
        icon: "shield",
        text: "PII redaction is irreversible at the client boundary. The response delivered to the caller is the redacted or rewritten version recorded in zeroshield metadata.",
      },
      {
        icon: "info",
        text: "Flag actions create Human Review Queue entries, while block actions create Security Incidents automatically. Treat those operator surfaces as the source of truth for post-generation enforcement follow-up.",
      },
    ],
  },
};

export const HOW_TO_USE = deepReplaceUrls(HOW_TO_USE_RAW);
