import { useState, useEffect, useCallback } from "react";
import {
  Plus, Pencil, Trash2, X, Loader2, Server,
  CheckCircle, AlertTriangle, Power, PowerOff,
  ChevronDown, ChevronRight, Search, Globe, KeyRound,
} from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { InfoTooltip } from "./InfoTooltip";
import {
  ZEROSHIELD_GUARD_MODEL,
  ZEROSHIELD_GUARD_MODEL_LABEL,
} from "../constants/zeroshieldBrand";

const PROVIDERS = [
  { value: "zeroshield", label: "ZeroShield", models: [ZEROSHIELD_GUARD_MODEL] },
  { value: "openai", label: "Open AI", models: ["gpt-5.2", "gpt-4.1", "gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo", "text-embedding-3-large", "gpt-5.2-codex", "gpt-image-1.5", "o1", "o1-mini"] },
  { value: "meta", label: "Meta Llama", models: ["llama-3-70b", "llama-3-8b", "llama-4-maverick-17b"] },
  { value: "mistral", label: "Mistral AI", models: ["mistral-large-3", "ministral-14b", "voxtral-realtime", "magistral-small-2509"] },
  { value: "huggingface", label: "Hugging Face", models: ["falcon-40b-instruct", "falcon-7b", "gpt-neox-20b", "gpt-j-6b", "vicuna-13b", "flan-t5-large"] },
  { value: "aws_bedrock", label: "AWS Bedrock", models: ["bedrock-llama-3"] },
  { value: "ollama", label: "Ollama (Local)", models: ["local-llama", "local-mistral", "local-codellama"] },
  { value: "custom", label: "Custom / Other", models: [] },
];

const MODEL_ID_MAP = {
  "gpt-5.2": "openai/gpt-5.2",
  "gpt-4.1": "openai/gpt-4.1",
  "gpt-4o": "openai/gpt-4o",
  "gpt-4o-mini": "openai/gpt-4o-mini",
  "gpt-4-turbo": "openai/gpt-4-turbo",
  "gpt-3.5-turbo": "openai/gpt-3.5-turbo",
  "text-embedding-3-large": "openai/text-embedding-3-large",
  "gpt-5.2-codex": "openai/gpt-5.2-codex",
  "gpt-image-1.5": "openai/gpt-image-1.5",
  "o1": "openai/o1",
  "o1-mini": "openai/o1-mini",
  "claude-opus-4.6": "anthropic/claude-opus-4-6",
  "claude-sonnet-4": "anthropic/claude-sonnet-4",
  "claude-3.7-sonnet": "anthropic/claude-3-7-sonnet",
  "claude-opus-code": "anthropic/claude-opus-code",
  "claude-3-opus": "anthropic/claude-3-opus-20240229",
  "claude-3-sonnet": "anthropic/claude-3-sonnet-20240229",
  "claude-3-haiku": "anthropic/claude-3-haiku-20240307",
  "claude-3.5-sonnet": "anthropic/claude-3.5-sonnet-20241022",
  "azure-gpt-4o": "azure/gpt-4o",
  "azure-gpt-4-turbo": "azure/gpt-4-turbo",
  "gemini-3.1-pro": "gemini/gemini-3.1-pro",
  "gemini-2.5-flash": "gemini/gemini-2.5-flash",
  "gemini-ultra": "gemini/gemini-ultra",
  "gemini-1.5-pro": "gemini/gemini-1.5-pro",
  "gemini-1.5-flash": "gemini/gemini-1.5-flash",
  "gemini-2.0-flash": "gemini/gemini-2.0-flash",
  "mistral-large-3": "mistral/mistral-large-latest",
  "ministral-14b": "mistral/ministral-2410",
  "voxtral-realtime": "mistral/voxtral-realtime",
  "magistral-small-2509": "mistral/magistral-small-2509",
  "command-r": "cohere_chat/command-r",
  "cohere-command": "cohere_chat/command",
  "cohere-embed-4": "cohere/embed-english-v4.0",
  "deepseek-v3.2": "deepseek/deepseek-chat",
  "llama-3-70b": "huggingface/meta-llama/Meta-Llama-3-70B-Instruct",
  "llama-3-8b": "huggingface/meta-llama/Meta-Llama-3-8B-Instruct",
  "llama-4-maverick-17b": "huggingface/meta-llama/llama4-maverick-17b",
  "falcon-40b-instruct": "huggingface/tiiuae/falcon-40b-instruct",
  "falcon-7b": "huggingface/tiiuae/falcon-7b",
  "gpt-neox-20b": "huggingface/EleutherAI/gpt-neox-20b",
  "gpt-j-6b": "huggingface/EleutherAI/gpt-j-6b",
  "vicuna-13b": "huggingface/lmsys/vicuna-13b-v1.5",
  "flan-t5-large": "huggingface/google/flan-t5-large",
  [ZEROSHIELD_GUARD_MODEL]: "bedrock/openai.gpt-oss-120b-1:0",
  "bedrock-llama-3": "bedrock/meta.llama3-1-70b-instruct-v1:0",
  "local-llama": "ollama/llama3",
  "local-mistral": "ollama/mistral",
  "local-codellama": "ollama/codellama",
};

const DEFAULT_API_KEY_ENV = {
  zeroshield: "AWS_ACCESS_KEY_ID",
  openai: "OPENAI_API_KEY",
  google: "GOOGLE_API_KEY",
  mistral: "MISTRAL_API_KEY",
  meta: "HF_TOKEN",
  huggingface: "HF_TOKEN",
  custom: "",
};

function displayModelName(modelName) {
  if (modelName === ZEROSHIELD_GUARD_MODEL) return ZEROSHIELD_GUARD_MODEL_LABEL;
  return modelName;
}

const INITIAL_FORM = {
  provider: "openai",
  model_name: "",
  model_id: "",
  api_key: "",
  api_key_env: "OPENAI_API_KEY",
  api_base_url: "",
  region: "",
  is_active: true,
  custom_model_name: "",
  data_sensitivity_level: "public",
  compliance_tags: "",
  cost_per_1k_input_tokens: "",
  cost_per_1k_output_tokens: "",
  latency_sla_ms: "",
  routing_priority: "",
  rate_limit_rpm: "",
};

const GATEWAY_URL_KEY = "zeroshield_gateway_url";
const GATEWAY_KEY_KEY = "zeroshield_gateway_api_key";
const PROVIDER_CREDENTIALS_KEY = "zeroshield_provider_credentials";

const PROVIDER_DISPLAY = {
  zeroshield: "ZeroShield",
  openai: "OpenAI",
  anthropic: "Anthropic",
  bedrock: "AWS Bedrock",
  aws_bedrock: "AWS Bedrock",
  azure: "Azure OpenAI",
  gemini: "Google Gemini",
  google: "Google",
  ollama: "Ollama",
  meta: "Meta",
  mistral: "Mistral AI",
  deepseek: "DeepSeek",
  minimax: "MiniMax",
  moonshot: "Moonshot AI",
  nvidia: "NVIDIA",
  qwen: "Qwen",
  zai: "Z.AI",
  custom: "Custom",
};

const INITIAL_PROVIDER_CREDENTIALS_FORM = {
  provider: "openai",
  apiKey: "",
  apiBaseUrl: "",
  region: "",
};

function loadProviderCredentials() {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(PROVIDER_CREDENTIALS_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function persistProviderCredentials(nextCredentials) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(PROVIDER_CREDENTIALS_KEY, JSON.stringify(nextCredentials));
}

function readLocalStorageValue(key) {
  if (typeof window === "undefined") return "";
  try {
    return window.localStorage.getItem(key) || "";
  } catch {
    return "";
  }
}

function maskCredential(secret) {
  if (!secret) return "";
  if (secret.length <= 8) return "•".repeat(secret.length);
  return `${secret.slice(0, 4)}${"•".repeat(Math.max(secret.length - 8, 4))}${secret.slice(-4)}`;
}

function formatApiError(errData, fallbackMessage) {
  if (!errData || typeof errData !== "object") return fallbackMessage;
  if (typeof errData.detail === "string" && errData.detail.trim()) return errData.detail;
  if (typeof errData.message === "string" && errData.message.trim()) return errData.message;

  const fieldErrors = Object.entries(errData)
    .filter(([key]) => key !== "detail" && key !== "message")
    .map(([key, value]) => {
      if (Array.isArray(value)) {
        return `${key}: ${value.join(", ")}`;
      }
      if (typeof value === "string") {
        return `${key}: ${value}`;
      }
      return null;
    })
    .filter(Boolean);

  return fieldErrors.length ? fieldErrors.join(" | ") : fallbackMessage;
}

export function ModelConnectionPanel({ showProviderForm = true, showConnectionsTable = true }) {
  const { fetchWithAuth } = useAuth();
  const [models, setModels] = useState([]);
  const [loading, setLoading] = useState(showConnectionsTable);
  const [modalOpen, setModalOpen] = useState(false);
  const [formData, setFormData] = useState(INITIAL_FORM);
  const [submitting, setSubmitting] = useState(false);
  const [actionLoading, setActionLoading] = useState(null);
  const [error, setError] = useState(null);
  const [editingModel, setEditingModel] = useState(null);
  const [routingExpanded, setRoutingExpanded] = useState(false);

  const [gatewayModels, setGatewayModels] = useState([]);
  const [gatewayModelsLoading, setGatewayModelsLoading] = useState(false);
  const [gatewayModelsExpanded, setGatewayModelsExpanded] = useState(true);
  const [gatewaySearch, setGatewaySearch] = useState("");
  const [gatewayProviderFilter, setGatewayProviderFilter] = useState("all");
  const [providerCredentials, setProviderCredentials] = useState(() => loadProviderCredentials());
  const [credentialsForm, setCredentialsForm] = useState(INITIAL_PROVIDER_CREDENTIALS_FORM);
  const [credentialsMessage, setCredentialsMessage] = useState("");
  const [providerApiKeys, setProviderApiKeys] = useState({});

  const fetchModels = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchWithAuth("/api/firewall/models/");
      if (res.ok) {
        const data = await res.json();
        setModels(Array.isArray(data) ? data : data.results || []);
      } else {
        setError(`Failed to load model configurations (${res.status}).`);
      }
    } catch (fetchError) {
      setModels([]);
      setError(fetchError?.message || "Failed to load model configurations.");
    } finally {
      setLoading(false);
    }
  }, [fetchWithAuth]);

  useEffect(() => {
    if (showConnectionsTable) {
      fetchModels();
    }
  }, [fetchModels, showConnectionsTable]);

  const fetchGatewayModels = useCallback(async () => {
    const stored = readLocalStorageValue(GATEWAY_URL_KEY);
    const gatewayUrl = stored || `http://${typeof window !== "undefined" ? window.location.hostname || "127.0.0.1" : "127.0.0.1"}:8300`;
    const gatewayKey = readLocalStorageValue(GATEWAY_KEY_KEY);
    setGatewayModelsLoading(true);
    try {
      const headers = {};
      if (gatewayKey) {
        headers["Authorization"] = `Bearer ${gatewayKey}`;
      }
      const res = await fetch(`${gatewayUrl.replace(/\/+$/, "")}/v1/models`, { headers });
      if (res.ok) {
        const data = await res.json();
        setGatewayModels(data.data || []);
      } else {
        setGatewayModels([]);
      }
    } catch {
      setGatewayModels([]);
    } finally {
      setGatewayModelsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (showConnectionsTable) {
      fetchGatewayModels();
    }
  }, [fetchGatewayModels, showConnectionsTable]);

  const selectedProvider = PROVIDERS.find((p) => p.value === formData.provider);
  const useCustomModelName = formData.model_name === "__custom__";
  const showBaseUrl = formData.provider === "custom";
  const showRegion = false;
  const activeApiKey = providerApiKeys[formData.provider] || "";
  const savedProviderCredential = providerCredentials[formData.provider] || null;
  const selectedCredentialEntry = providerCredentials[credentialsForm.provider] || null;

  useEffect(() => {
    const nextCredential = providerCredentials[credentialsForm.provider];
    setCredentialsForm((current) => ({
      ...current,
      apiKey: nextCredential?.apiKey || "",
      apiBaseUrl: nextCredential?.apiBaseUrl || "",
      region: nextCredential?.region || "",
    }));
  }, [credentialsForm.provider, providerCredentials]);

  const handleProviderChange = (provider) => {
    const envVar = DEFAULT_API_KEY_ENV[provider] || "";
    const nextCredential = providerCredentials[provider] || null;
    setFormData({
      ...formData,
      provider,
      model_name: "",
      model_id: "",
      api_key: "",
      api_key_env: envVar,
      api_base_url: nextCredential?.apiBaseUrl || "",
      region: nextCredential?.region || "",
      custom_model_name: "",
    });
  };

  const handleModelNameChange = (modelName) => {
    const modelId = MODEL_ID_MAP[modelName] || "";
    setFormData({
      ...formData,
      model_name: modelName,
      model_id: modelId,
      custom_model_name: "",
    });
  };

  const openCreateModal = () => {
    setFormData(INITIAL_FORM);
    setEditingModel(null);
    setRoutingExpanded(false);
    setError(null);
    setModalOpen(true);
  };

  const handleCredentialsProviderChange = (provider) => {
    const nextCredential = providerCredentials[provider] || null;
    setCredentialsForm({
      provider,
      apiKey: nextCredential?.apiKey || "",
      apiBaseUrl: nextCredential?.apiBaseUrl || "",
      region: nextCredential?.region || "",
    });
    setCredentialsMessage("");
  };

  const handleSaveProviderCredentials = () => {
    if (!credentialsForm.apiKey.trim()) {
      setCredentialsMessage("Enter an API key before saving provider credentials.");
      return;
    }

    const nextCredentials = {
      ...providerCredentials,
      [credentialsForm.provider]: {
        apiKey: credentialsForm.apiKey.trim(),
        apiBaseUrl: credentialsForm.apiBaseUrl.trim(),
        region: credentialsForm.region.trim(),
        updatedAt: new Date().toISOString(),
      },
    };
    persistProviderCredentials(nextCredentials);
    setProviderCredentials(nextCredentials);
    setCredentialsMessage(`${getProviderLabel(credentialsForm.provider)} credentials saved locally in this browser.`);
  };

  const handleDeleteProviderCredentials = (provider) => {
    const nextCredentials = { ...providerCredentials };
    delete nextCredentials[provider];
    persistProviderCredentials(nextCredentials);
    setProviderCredentials(nextCredentials);
    setCredentialsForm((current) => current.provider === provider ? {
      provider,
      apiKey: "",
      apiBaseUrl: "",
      region: "",
    } : current);
    setCredentialsMessage(`${getProviderLabel(provider)} credentials removed from this browser.`);
  };

  const handleApplySavedProviderSettings = () => {
    if (!savedProviderCredential) return;
    setFormData((current) => ({
      ...current,
      api_base_url: savedProviderCredential.apiBaseUrl || current.api_base_url,
      region: savedProviderCredential.region || current.region,
    }));
  };

  const openEditModal = (model) => {
    setFormData({
      provider: model.provider || "openai",
      model_name: model.model_name || "",
      model_id: model.model_id || "",
      api_key_env: model.api_key_env_var || "",
      api_base_url: model.api_base || "",
      region: model.region || "",
      is_active: model.is_active,
      custom_model_name: "",
      data_sensitivity_level: model.data_sensitivity_level || "public",
      compliance_tags: Array.isArray(model.compliance_tags) ? model.compliance_tags.join(", ") : (model.compliance_tags || ""),
      cost_per_1k_input_tokens: model.cost_per_1k_input_tokens ?? "",
      cost_per_1k_output_tokens: model.cost_per_1k_output_tokens ?? "",
      latency_sla_ms: model.latency_sla_ms ?? "",
      routing_priority: model.routing_priority ?? "",
      rate_limit_rpm: model.rate_limit_rpm ?? "",
    });
    setEditingModel(model);
    setRoutingExpanded(true);
    setError(null);
    setModalOpen(true);
  };

  const handleSave = async (e) => {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const resolvedModelName = useCustomModelName
        ? formData.custom_model_name
        : formData.model_name;

      const payload = {
        provider: formData.provider,
        model_name: resolvedModelName,
        model_id: formData.model_id,
        api_key_env_var: formData.api_key_env,
        is_active: editingModel ? formData.is_active : true,
        data_sensitivity_level: formData.data_sensitivity_level || "public",
        compliance_tags: formData.compliance_tags
          ? formData.compliance_tags.split(",").map((t) => t.trim()).filter(Boolean)
          : [],
      };

      const assignOptionalNumericField = ({ key, label, rawValue, parseFn }) => {
        if (rawValue === "") {
          if (editingModel) {
            // For edits, explicit null means "reset to backend default".
            payload[key] = null;
          }
          return true;
        }
        const parsed = parseFn(rawValue);
        if (!Number.isFinite(parsed)) {
          setError(`${label} must be a valid number.`);
          return false;
        }
        if (parsed < 0) {
          setError(`${label} cannot be negative.`);
          return false;
        }
        payload[key] = parsed;
        return true;
      };

      const numericFieldSpecs = [
        {
          key: "cost_per_1k_input_tokens",
          label: "Cost per 1K input tokens",
          rawValue: formData.cost_per_1k_input_tokens,
          parseFn: (v) => parseFloat(v),
        },
        {
          key: "cost_per_1k_output_tokens",
          label: "Cost per 1K output tokens",
          rawValue: formData.cost_per_1k_output_tokens,
          parseFn: (v) => parseFloat(v),
        },
        {
          key: "latency_sla_ms",
          label: "Latency SLA",
          rawValue: formData.latency_sla_ms,
          parseFn: (v) => parseInt(v, 10),
        },
        {
          key: "routing_priority",
          label: "Routing priority",
          rawValue: formData.routing_priority,
          parseFn: (v) => parseInt(v, 10),
        },
        {
          key: "rate_limit_rpm",
          label: "Rate limit (RPM)",
          rawValue: formData.rate_limit_rpm,
          parseFn: (v) => parseInt(v, 10),
        },
      ];

      for (const field of numericFieldSpecs) {
        if (!assignOptionalNumericField(field)) {
          return;
        }
      }
      if (formData.api_base_url.trim()) {
        payload.api_base = formData.api_base_url.trim();
      }
      if (formData.region.trim()) {
        payload.region = formData.region.trim();
      }

      const url = editingModel
        ? `/api/firewall/models/${editingModel.id}/`
        : "/api/firewall/models/";
      const method = editingModel ? "PUT" : "POST";

      const res = await fetchWithAuth(url, {
        method,
        body: JSON.stringify(payload),
      });
      if (res.ok) {
        setModalOpen(false);
        setEditingModel(null);
        await fetchModels();
      } else {
        const errData = await res.json().catch(() => ({}));
        setError(
          formatApiError(
            errData,
            `Failed to ${editingModel ? "update" : "create"} model configuration.`,
          ),
        );
      }
    } finally {
      setSubmitting(false);
    }
  };

  const handleToggleActive = async (model) => {
    setActionLoading(model.id);
    try {
      await fetchWithAuth(`/api/firewall/models/${model.id}/`, {
        method: "PATCH",
        body: JSON.stringify({ is_active: !model.is_active }),
      });
      await fetchModels();
    } finally {
      setActionLoading(null);
    }
  };

  const handleDelete = async (id) => {
    if (!window.confirm("Delete this model configuration? This action cannot be undone.")) return;
    setActionLoading(id);
    try {
      await fetchWithAuth(`/api/firewall/models/${id}/`, { method: "DELETE" });
      await fetchModels();
    } finally {
      setActionLoading(null);
    }
  };

  const getProviderLabel = (providerValue) => {
    const provider = PROVIDERS.find((p) => p.value === providerValue);
    return provider ? provider.label : providerValue;
  };

  return (
    <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl shadow-sm p-6">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100 flex items-center gap-2">
            <Server className="w-4 h-4 text-teal-600" />
            {showConnectionsTable ? "LLM Model Connections" : "LLM Router & Model Provider"}
            <InfoTooltip title="How to Use">{"Configure LLM providers and models available through the gateway. Set routing priority, per-model rate limits, data sensitivity levels, and cost parameters for dynamic routing decisions."}</InfoTooltip>
          </h3>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
            {showConnectionsTable
              ? "Manage LLM provider configurations and model routing"
              : "Select a provider and configure API credentials"
            }
          </p>
        </div>
        {showConnectionsTable && (
          <button
            onClick={openCreateModal}
            className="flex items-center gap-1.5 px-3 py-2 bg-teal-600 hover:bg-teal-700 text-white text-xs font-medium rounded-lg transition-colors"
          >
            <Plus className="w-3.5 h-3.5" />
            Add Model
          </button>
        )}
      </div>

      <div className="mb-6 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50/70 dark:bg-slate-900/30 p-4">
        <div className="flex items-start justify-between gap-3 mb-4">
          <div>
            <h4 className="text-sm font-semibold text-slate-900 dark:text-slate-100 flex items-center gap-2">
              <KeyRound className="w-4 h-4 text-teal-600" />
              Provider API Keys
              <InfoTooltip title="How to Use">{"Store provider API keys locally in this browser while you onboard models. The gateway still reads provider credentials from the env-var name configured on each model, so this panel is for operator setup and reference rather than backend secret storage."}</InfoTooltip>
            </h4>
            <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
              Add provider credentials directly on the routing page instead of relying on hidden environment-variable knowledge.
            </p>
          </div>
          <div className="rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-900/20 px-3 py-2 text-[11px] text-amber-800 dark:text-amber-200 max-w-xs">
            Stored locally only. Runtime routing still uses the API key env var configured on each model.
          </div>
        </div>

        <div className="grid gap-3 md:grid-cols-2">
          <div>
            <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Provider</label>
            <select
              value={credentialsForm.provider}
              onChange={(e) => handleCredentialsProviderChange(e.target.value)}
              className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent"
            >
              {PROVIDERS.map((p) => (
                <option key={p.value} value={p.value}>{p.label}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">API Key</label>
            <input
              type="password"
              value={credentialsForm.apiKey}
              onChange={(e) => setCredentialsForm((current) => ({ ...current, apiKey: e.target.value }))}
              placeholder="Paste provider API key"
              className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm font-mono focus:ring-2 focus:ring-teal-500 focus:border-transparent"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">API Base URL</label>
            <input
              type="text"
              value={credentialsForm.apiBaseUrl}
              onChange={(e) => setCredentialsForm((current) => ({ ...current, apiBaseUrl: e.target.value }))}
              placeholder="Optional override endpoint"
              className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm font-mono focus:ring-2 focus:ring-teal-500 focus:border-transparent"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Region</label>
            <input
              type="text"
              value={credentialsForm.region}
              onChange={(e) => setCredentialsForm((current) => ({ ...current, region: e.target.value }))}
              placeholder="Optional region"
              className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent"
            />
          </div>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={handleSaveProviderCredentials}
            className="px-3 py-2 rounded-lg bg-teal-600 hover:bg-teal-700 text-white text-xs font-medium transition-colors"
          >
            Save Provider Key
          </button>
          {selectedCredentialEntry && (
            <button
              type="button"
              onClick={() => handleDeleteProviderCredentials(credentialsForm.provider)}
              className="px-3 py-2 rounded-lg border border-red-200 dark:border-red-800 text-red-600 dark:text-red-300 text-xs font-medium hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors"
            >
              Remove Saved Key
            </button>
          )}
          {credentialsMessage && (
            <span className="text-xs text-slate-600 dark:text-slate-300">{credentialsMessage}</span>
          )}
        </div>

        <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {PROVIDERS.filter((provider) => providerCredentials[provider.value]).map((provider) => {
            const entry = providerCredentials[provider.value];
            return (
              <div key={provider.value} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-3">
                <div className="flex items-center justify-between gap-2">
                  <div>
                    <div className="text-xs font-semibold text-slate-800 dark:text-slate-100">{provider.label}</div>
                    <div className="text-[11px] font-mono text-slate-500 dark:text-slate-400">{maskCredential(entry.apiKey)}</div>
                  </div>
                  <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 dark:bg-emerald-800/30 px-2 py-0.5 text-[10px] font-medium text-emerald-700 dark:text-emerald-300">
                    <CheckCircle className="w-3 h-3" /> Saved
                  </span>
                </div>
                <div className="mt-2 space-y-1 text-[11px] text-slate-500 dark:text-slate-400">
                  <div>Env var: <span className="font-mono text-slate-700 dark:text-slate-300">{DEFAULT_API_KEY_ENV[provider.value] || "custom"}</span></div>
                  <div>Base URL: <span className="font-mono text-slate-700 dark:text-slate-300">{entry.apiBaseUrl || "default"}</span></div>
                  <div>Region: <span className="font-mono text-slate-700 dark:text-slate-300">{entry.region || "--"}</span></div>
                </div>
              </div>
            );
          })}
          {Object.keys(providerCredentials).length === 0 && (
            <div className="md:col-span-2 xl:col-span-3 rounded-lg border border-dashed border-slate-300 dark:border-slate-700 px-4 py-5 text-xs text-slate-500 dark:text-slate-400 text-center">
              No provider API keys saved yet. Add one here so model onboarding has a visible setup path.
            </div>
          )}
        </div>
      </div>

      <div className="mb-6 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50/70 dark:bg-slate-900/30 p-4">
        <div className="flex items-start justify-between gap-3 mb-4">
          <div>
            <h4 className="text-sm font-semibold text-slate-900 dark:text-slate-100 flex items-center gap-2">
              <KeyRound className="w-4 h-4 text-teal-600" />
              Provider API Keys
              <InfoTooltip title="How to Use">{"Store provider API keys locally in this browser while you onboard models. The gateway still reads provider credentials from the env-var name configured on each model, so this panel is for operator setup and reference rather than backend secret storage."}</InfoTooltip>
            </h4>
            <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
              Add provider credentials directly on the routing page instead of relying on hidden environment-variable knowledge.
            </p>
          </div>
          <div className="rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-900/20 px-3 py-2 text-[11px] text-amber-800 dark:text-amber-200 max-w-xs">
            Stored locally only. Runtime routing still uses the API key env var configured on each model.
          </div>
        </div>

        <div className="grid gap-3 md:grid-cols-2">
          <div>
            <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Provider</label>
            <select
              value={credentialsForm.provider}
              onChange={(e) => handleCredentialsProviderChange(e.target.value)}
              className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent"
            >
              {PROVIDERS.map((p) => (
                <option key={p.value} value={p.value}>{p.label}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">API Key</label>
            <input
              type="password"
              value={credentialsForm.apiKey}
              onChange={(e) => setCredentialsForm((current) => ({ ...current, apiKey: e.target.value }))}
              placeholder="Paste provider API key"
              className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm font-mono focus:ring-2 focus:ring-teal-500 focus:border-transparent"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">API Base URL</label>
            <input
              type="text"
              value={credentialsForm.apiBaseUrl}
              onChange={(e) => setCredentialsForm((current) => ({ ...current, apiBaseUrl: e.target.value }))}
              placeholder="Optional override endpoint"
              className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm font-mono focus:ring-2 focus:ring-teal-500 focus:border-transparent"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Region</label>
            <input
              type="text"
              value={credentialsForm.region}
              onChange={(e) => setCredentialsForm((current) => ({ ...current, region: e.target.value }))}
              placeholder="Optional region"
              className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent"
            />
          </div>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={handleSaveProviderCredentials}
            className="px-3 py-2 rounded-lg bg-teal-600 hover:bg-teal-700 text-white text-xs font-medium transition-colors"
          >
            Save Provider Key
          </button>
          {selectedCredentialEntry && (
            <button
              type="button"
              onClick={() => handleDeleteProviderCredentials(credentialsForm.provider)}
              className="px-3 py-2 rounded-lg border border-red-200 dark:border-red-800 text-red-600 dark:text-red-300 text-xs font-medium hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors"
            >
              Remove Saved Key
            </button>
          )}
          {credentialsMessage && (
            <span className="text-xs text-slate-600 dark:text-slate-300">{credentialsMessage}</span>
          )}
        </div>

        <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {PROVIDERS.filter((provider) => providerCredentials[provider.value]).map((provider) => {
            const entry = providerCredentials[provider.value];
            return (
              <div key={provider.value} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-3">
                <div className="flex items-center justify-between gap-2">
                  <div>
                    <div className="text-xs font-semibold text-slate-800 dark:text-slate-100">{provider.label}</div>
                    <div className="text-[11px] font-mono text-slate-500 dark:text-slate-400">{maskCredential(entry.apiKey)}</div>
                  </div>
                  <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 dark:bg-emerald-800/30 px-2 py-0.5 text-[10px] font-medium text-emerald-700 dark:text-emerald-300">
                    <CheckCircle className="w-3 h-3" /> Saved
                  </span>
                </div>
                <div className="mt-2 space-y-1 text-[11px] text-slate-500 dark:text-slate-400">
                  <div>Env var: <span className="font-mono text-slate-700 dark:text-slate-300">{DEFAULT_API_KEY_ENV[provider.value] || "custom"}</span></div>
                  <div>Base URL: <span className="font-mono text-slate-700 dark:text-slate-300">{entry.apiBaseUrl || "default"}</span></div>
                  <div>Region: <span className="font-mono text-slate-700 dark:text-slate-300">{entry.region || "--"}</span></div>
                </div>
              </div>
            );
          })}
          {Object.keys(providerCredentials).length === 0 && (
            <div className="md:col-span-2 xl:col-span-3 rounded-lg border border-dashed border-slate-300 dark:border-slate-700 px-4 py-5 text-xs text-slate-500 dark:text-slate-400 text-center">
              No provider API keys saved yet. Add one here so model onboarding has a visible setup path.
            </div>
          )}
        </div>
      </div>

      {showProviderForm && (
      <div className="mb-5 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900/30 p-4">
        <h4 className="text-sm font-semibold text-slate-900 dark:text-slate-100 mb-3">LLM Router &amp; Model Provider</h4>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Cloud/Local Provider</label>
            <p className="text-[11px] text-slate-500 dark:text-slate-400 mb-2">Select provider to configure API credentials</p>
            <select
              value={formData.provider}
              onChange={(e) => handleProviderChange(e.target.value)}
              className="w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100 focus:ring-2 focus:ring-teal-500 focus:border-transparent"
            >
              {PROVIDERS.map((p) => (
                <option key={p.value} value={p.value}>{p.label}</option>
              ))}
            </select>
          </div>

          <div>
            <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">
              {selectedProvider?.label || "Provider"} API Key
            </label>
            <p className="text-[11px] text-slate-500 dark:text-slate-400 mb-2">This box appears dynamically based on the selected provider</p>
            <input
              type="password"
              value={activeApiKey}
              onChange={(e) =>
                setProviderApiKeys((prev) => ({ ...prev, [formData.provider]: e.target.value }))
              }
              placeholder={`Enter ${selectedProvider?.label || "provider"} API key`}
              className="w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100 focus:ring-2 focus:ring-teal-500 focus:border-transparent"
            />
          </div>
        </div>
      </div>
      )}

      {showConnectionsTable && (loading ? (
        <div className="flex items-center justify-center py-8">
          <Loader2 className="w-5 h-5 text-teal-500 animate-spin" />
          <span className="ml-2 text-sm text-slate-500 dark:text-slate-400">Loading model configurations...</span>
        </div>
      ) : models.length === 0 ? (
        <div className="text-center py-8 text-sm text-slate-500 dark:text-slate-400">
          No model configurations found. Add one to configure LLM routing.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-slate-50 dark:bg-slate-800/50 border-b border-slate-200 dark:border-slate-700">
                <th className="px-3 py-2.5 text-left text-xs font-semibold text-slate-600 dark:text-slate-300 uppercase">Provider</th>
                <th className="px-3 py-2.5 text-left text-xs font-semibold text-slate-600 dark:text-slate-300 uppercase">Model Name</th>
                <th className="px-3 py-2.5 text-left text-xs font-semibold text-slate-600 dark:text-slate-300 uppercase">Model ID</th>
                <th className="px-3 py-2.5 text-left text-xs font-semibold text-slate-600 dark:text-slate-300 uppercase">API Key Env</th>
                <th className="px-3 py-2.5 text-left text-xs font-semibold text-slate-600 dark:text-slate-300 uppercase">Region</th>
                <th className="px-3 py-2.5 text-left text-xs font-semibold text-slate-600 dark:text-slate-300 uppercase">Sensitivity</th>
                <th className="px-3 py-2.5 text-left text-xs font-semibold text-slate-600 dark:text-slate-300 uppercase">Priority</th>
                <th className="px-3 py-2.5 text-left text-xs font-semibold text-slate-600 dark:text-slate-300 uppercase">Status</th>
                <th className="px-3 py-2.5 text-right text-xs font-semibold text-slate-600 dark:text-slate-300 uppercase">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-700">
              {models.map((m) => (
                <tr key={m.id} className="hover:bg-slate-50 dark:hover:bg-slate-700 transition-colors">
                  <td className="px-3 py-2.5 text-xs font-medium text-slate-800 dark:text-slate-200">
                    {getProviderLabel(m.provider)}
                  </td>
                  <td className="px-3 py-2.5 font-mono text-xs text-slate-600 dark:text-slate-400">{displayModelName(m.model_name)}</td>
                  <td className="px-3 py-2.5 font-mono text-xs text-slate-600 dark:text-slate-400 max-w-[200px] truncate">
                    {m.model_name === ZEROSHIELD_GUARD_MODEL ? ZEROSHIELD_GUARD_MODEL_LABEL : (m.model_id || "--")}
                  </td>
                  <td className="px-3 py-2.5 font-mono text-xs text-slate-600 dark:text-slate-400">{m.api_key_env_var || "--"}</td>
                  <td className="px-3 py-2.5 text-xs text-slate-600 dark:text-slate-400">{m.region || "--"}</td>
                  <td className="px-3 py-2.5">
                    <span className={`inline-flex px-1.5 py-0.5 rounded text-[10px] font-medium ${
                      m.data_sensitivity_level === "restricted" ? "bg-red-100 dark:bg-red-800/30 text-red-700 dark:text-red-300" :
                      m.data_sensitivity_level === "confidential" ? "bg-amber-100 dark:bg-amber-800/30 text-amber-700 dark:text-amber-300" :
                      m.data_sensitivity_level === "internal" ? "bg-blue-100 dark:bg-blue-800/30 text-blue-700 dark:text-blue-300" :
                      "bg-slate-100 dark:bg-slate-700/50 text-slate-600 dark:text-slate-400"
                    }`}>{m.data_sensitivity_level || "public"}</span>
                  </td>
                  <td className="px-3 py-2.5 text-xs font-mono text-slate-600 dark:text-slate-400">{m.routing_priority ?? "--"}</td>
                  <td className="px-3 py-2.5">
                    {m.is_active ? (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-emerald-100 dark:bg-emerald-800/30 text-emerald-700 dark:text-emerald-300">
                        <CheckCircle className="w-3 h-3" /> Active
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-red-100 dark:bg-red-800/30 text-red-700 dark:text-red-300">
                        <AlertTriangle className="w-3 h-3" /> Disabled
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2.5">
                    <div className="flex items-center justify-end gap-1">
                      {actionLoading === m.id ? (
                        <Loader2 className="w-4 h-4 text-teal-500 animate-spin" />
                      ) : (
                        <>
                          <button
                            onClick={() => openEditModal(m)}
                            className="p-1.5 hover:bg-slate-100 dark:hover:bg-slate-700 rounded text-slate-500 transition-colors"
                            title="Edit routing config"
                          >
                            <Pencil className="w-3.5 h-3.5" />
                          </button>
                          <button
                            onClick={() => handleToggleActive(m)}
                            className={`p-1.5 rounded transition-colors ${
                              m.is_active
                                ? "hover:bg-red-50 dark:hover:bg-red-900/20 text-red-500"
                                : "hover:bg-emerald-50 dark:bg-emerald-900/20 text-emerald-600"
                            }`}
                            title={m.is_active ? "Disable" : "Enable"}
                          >
                            {m.is_active ? (
                              <PowerOff className="w-3.5 h-3.5" />
                            ) : (
                              <Power className="w-3.5 h-3.5" />
                            )}
                          </button>
                          <button
                            onClick={() => handleDelete(m.id)}
                            className="p-1.5 hover:bg-red-50 dark:hover:bg-red-900/20 rounded text-red-500 transition-colors"
                            title="Delete"
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        </>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}

      {showConnectionsTable && (
      <div className="mt-6 border-t border-slate-200 dark:border-slate-700 pt-6">
        <button
          onClick={() => setGatewayModelsExpanded(!gatewayModelsExpanded)}
          className="flex items-center gap-2 w-full text-left mb-3"
        >
          {gatewayModelsExpanded ? (
            <ChevronDown className="w-4 h-4 text-slate-500 dark:text-slate-400" />
          ) : (
            <ChevronRight className="w-4 h-4 text-slate-500 dark:text-slate-400" />
          )}
          <Globe className="w-4 h-4 text-teal-600" />
          <span className="text-sm font-semibold text-slate-800 dark:text-slate-200">
            Pre-configured Gateway Models
          </span>
          {gatewayModels.length > 0 && (
            <span className="text-[10px] font-medium px-2 py-0.5 rounded-full bg-teal-100 dark:bg-teal-800/30 text-teal-700">
              {gatewayModels.length}
            </span>
          )}
        </button>

        {gatewayModelsExpanded && (
          <>
            {gatewayModelsLoading ? (
              <div className="flex items-center justify-center py-6">
                <Loader2 className="w-4 h-4 text-teal-500 animate-spin" />
                <span className="ml-2 text-xs text-slate-500 dark:text-slate-400">Loading gateway models...</span>
              </div>
            ) : gatewayModels.length === 0 ? (
              <div className="text-center py-6 text-xs text-slate-500 dark:text-slate-400">
                Could not load gateway models. Ensure the gateway is running.
              </div>
            ) : (
              <>
                <div className="flex items-center gap-2 mb-3">
                  <div className="relative flex-1">
                    <Search className="w-3.5 h-3.5 text-slate-400 absolute left-2.5 top-1/2 -translate-y-1/2" />
                    <input
                      type="text"
                      value={gatewaySearch}
                      onChange={(e) => setGatewaySearch(e.target.value)}
                      placeholder="Search models..."
                      className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full pl-8 pr-3 py-1.5 border border-slate-200 dark:border-slate-700 rounded-lg text-xs focus:ring-2 focus:ring-teal-500 focus:border-transparent"
                    />
                  </div>
                  <select
                    value={gatewayProviderFilter}
                    onChange={(e) => setGatewayProviderFilter(e.target.value)}
                    className="bg-white dark:bg-slate-800 px-2 py-1.5 border border-slate-200 dark:border-slate-700 rounded-lg text-xs text-slate-700 dark:text-slate-300 focus:ring-2 focus:ring-teal-500 focus:border-transparent"
                  >
                    <option value="all">All Providers</option>
                    {[...new Set(gatewayModels.map((m) => m.owned_by))].sort().map((p) => (
                      <option key={p} value={p}>{PROVIDER_DISPLAY[p] || p}</option>
                    ))}
                  </select>
                </div>

                <div className="overflow-x-auto max-h-[400px] overflow-y-auto border border-slate-200 dark:border-slate-700 rounded-lg">
                  <table className="w-full text-sm">
                    <thead className="sticky top-0 z-10">
                      <tr className="bg-slate-50 dark:bg-slate-800/50 border-b border-slate-200 dark:border-slate-700">
                        <th className="px-3 py-2 text-left text-[10px] font-semibold text-slate-600 dark:text-slate-300 uppercase">Provider</th>
                        <th className="px-3 py-2 text-left text-[10px] font-semibold text-slate-600 dark:text-slate-300 uppercase">Model Name</th>
                        <th className="px-3 py-2 text-left text-[10px] font-semibold text-slate-600 dark:text-slate-300 uppercase">Model ID</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100 dark:divide-slate-700">
                      {gatewayModels
                        .filter((m) => {
                          if (gatewayProviderFilter !== "all" && m.owned_by !== gatewayProviderFilter) return false;
                          if (gatewaySearch) {
                            const q = gatewaySearch.toLowerCase();
                            return (
                              m.id.toLowerCase().includes(q) ||
                              (m.model_id || "").toLowerCase().includes(q) ||
                              (m.owned_by || "").toLowerCase().includes(q)
                            );
                          }
                          return true;
                        })
                        .map((m) => (
                          <tr key={m.id} className="hover:bg-slate-50 dark:hover:bg-slate-700 transition-colors">
                            <td className="px-3 py-1.5 text-xs font-medium text-slate-700 dark:text-slate-300">
                              {PROVIDER_DISPLAY[m.owned_by] || m.owned_by}
                            </td>
                            <td className="px-3 py-1.5 font-mono text-xs text-slate-600 dark:text-slate-400">{m.id}</td>
                            <td className="px-3 py-1.5 font-mono text-[10px] text-slate-500 dark:text-slate-400 max-w-[280px] truncate">
                              {m.model_id || "--"}
                            </td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </>
        )}
      </div>
      )}

      {showConnectionsTable && modalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div className="fixed inset-0 bg-black/40" onClick={() => { setModalOpen(false); setEditingModel(null); }} />
          <div className="relative bg-white dark:bg-slate-800 rounded-xl shadow-xl border border-slate-200 dark:border-slate-700 w-full max-w-lg max-h-[90vh] overflow-y-auto p-6">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100">{editingModel ? "Edit Model Configuration" : "Add Model Configuration"}</h3>
              <button onClick={() => { setModalOpen(false); setEditingModel(null); }} className="p-1 hover:bg-slate-100 dark:hover:bg-slate-700 rounded transition-colors">
                <X className="w-4 h-4 text-slate-500 dark:text-slate-400" />
              </button>
            </div>

            {error && (
              <div className="p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg text-xs text-red-700 flex items-start gap-2 mb-4">
                <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
                <div>{error}</div>
              </div>
            )}

            <form onSubmit={handleSave} className="space-y-4">
              <div>
                <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Provider *</label>
                <select
                  value={formData.provider}
                  onChange={(e) => handleProviderChange(e.target.value)}
                  className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent"
                >
                  {PROVIDERS.map((p) => (
                    <option key={p.value} value={p.value}>{p.label}</option>
                  ))}
                </select>
              </div>

              <div className="rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900/30 p-3">
                <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">
                  {selectedProvider?.label || "Provider"} API Key
                </label>
                <input
                  type="password"
                  value={activeApiKey}
                  onChange={(e) =>
                    setProviderApiKeys((prev) => ({ ...prev, [formData.provider]: e.target.value }))
                  }
                  placeholder={`Enter ${selectedProvider?.label || "provider"} API key`}
                  className="w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100 focus:ring-2 focus:ring-teal-500 focus:border-transparent"
                />
                <p className="text-[10px] text-slate-500 dark:text-slate-400 mt-1">
                  This key is stored in the browser session for configuration convenience.
                </p>
              </div>

              <div>
                <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Model Name *</label>
                {selectedProvider && selectedProvider.models.length > 0 ? (
                  <select
                    value={formData.model_name}
                    onChange={(e) => handleModelNameChange(e.target.value)}
                    required
                    className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent"
                  >
                    <option value="">Select a model...</option>
                    {selectedProvider.models.map((m) => (
                      <option key={m} value={m}>{m}</option>
                    ))}
                    <option value="__custom__">Other (Custom)</option>
                  </select>
                ) : (
                  <input
                    type="text"
                    required
                    value={formData.custom_model_name}
                    onChange={(e) => setFormData({ ...formData, custom_model_name: e.target.value, model_name: "__custom__" })}
                    placeholder="Enter custom model name"
                    className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent"
                  />
                )}
              </div>

              {useCustomModelName && selectedProvider && selectedProvider.models.length > 0 && (
                <div>
                  <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Custom Model Name *</label>
                  <input
                    type="text"
                    required
                    value={formData.custom_model_name}
                    onChange={(e) => setFormData({ ...formData, custom_model_name: e.target.value })}
                    placeholder="Enter custom model name"
                    className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent"
                  />
                </div>
              )}

              <div>
                <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Model ID</label>
                <input
                  type="text"
                  value={formData.model_id}
                  onChange={(e) => setFormData({ ...formData, model_id: e.target.value })}
                  placeholder="e.g. openai/gpt-4o"
                  className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm font-mono focus:ring-2 focus:ring-teal-500 focus:border-transparent"
                />
                <p className="text-[10px] text-slate-400 mt-1">Auto-populated from model selection. Override if needed.</p>
              </div>

              <div>
                <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">API Key Env Variable</label>
                <input
                  type="text"
                  value={formData.api_key_env}
                  onChange={(e) => setFormData({ ...formData, api_key_env: e.target.value })}
                  placeholder="e.g. OPENAI_API_KEY"
                  className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm font-mono focus:ring-2 focus:ring-teal-500 focus:border-transparent"
                />
                <div className="mt-1.5 rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900/30 px-3 py-2 text-[11px] text-slate-500 dark:text-slate-400">
                  {savedProviderCredential ? (
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span>
                        Local {getProviderLabel(formData.provider)} key saved as {maskCredential(savedProviderCredential.apiKey)}.
                        The gateway still reads <span className="font-mono text-slate-700 dark:text-slate-300">{formData.api_key_env || DEFAULT_API_KEY_ENV[formData.provider] || "an env var"}</span> at runtime.
                      </span>
                      {(savedProviderCredential.apiBaseUrl || savedProviderCredential.region) && (
                        <button
                          type="button"
                          onClick={handleApplySavedProviderSettings}
                          className="rounded-md border border-slate-300 dark:border-slate-600 px-2 py-1 text-[10px] font-medium text-slate-700 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
                        >
                          Apply saved endpoint settings
                        </button>
                      )}
                    </div>
                  ) : (
                    <span>
                      No local {getProviderLabel(formData.provider)} key saved yet. Use the Provider API Keys panel above if you want to capture the provider key directly in the frontend.
                    </span>
                  )}
                </div>
              </div>

              {showBaseUrl && (
                <div>
                  <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">
                    API Base URL {formData.provider === "ollama" ? "*" : "(optional)"}
                  </label>
                  <input
                    type="text"
                    value={formData.api_base_url}
                    onChange={(e) => setFormData({ ...formData, api_base_url: e.target.value })}
                    placeholder={formData.provider === "ollama" ? "http://localhost:11434" : "https://api.example.com/v1"}
                    required={formData.provider === "ollama"}
                    className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm font-mono focus:ring-2 focus:ring-teal-500 focus:border-transparent"
                  />
                </div>
              )}

              {!showBaseUrl && (
                <div>
                  <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">API Base URL (optional)</label>
                  <input
                    type="text"
                    value={formData.api_base_url}
                    onChange={(e) => setFormData({ ...formData, api_base_url: e.target.value })}
                    placeholder="Leave empty for default"
                    className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm font-mono focus:ring-2 focus:ring-teal-500 focus:border-transparent"
                  />
                </div>
              )}

              {showRegion && (
                <div>
                  <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Region</label>
                  <input
                    type="text"
                    value={formData.region}
                    onChange={(e) => setFormData({ ...formData, region: e.target.value })}
                    placeholder={formData.provider === "aws_bedrock" ? "us-east-1" : "eastus"}
                    className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent"
                  />
                </div>
              )}

              {/* Routing Configuration */}
              <div className="border-t border-slate-200 dark:border-slate-700 pt-4 mt-4">
                <button
                  type="button"
                  onClick={() => setRoutingExpanded(!routingExpanded)}
                  className="flex items-center gap-2 text-xs font-semibold text-slate-700 dark:text-slate-300 mb-3 w-full text-left"
                >
                  {routingExpanded ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
                  Routing Configuration
                  <span className="text-[10px] font-normal text-slate-400">(optional)</span>
                </button>
                {routingExpanded && (
                  <div className="space-y-3">
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Data Sensitivity</label>
                        <select
                          value={formData.data_sensitivity_level}
                          onChange={(e) => setFormData({ ...formData, data_sensitivity_level: e.target.value })}
                          className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent"
                        >
                          <option value="public">Public</option>
                          <option value="internal">Internal</option>
                          <option value="confidential">Confidential</option>
                          <option value="restricted">Restricted</option>
                        </select>
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Routing Priority</label>
                        <input
                          type="number"
                          min="0"
                          max="100"
                          value={formData.routing_priority}
                          onChange={(e) => setFormData({ ...formData, routing_priority: e.target.value })}
                          placeholder="0-100"
                          className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent"
                        />
                      </div>
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Compliance Tags</label>
                      <input
                        type="text"
                        value={formData.compliance_tags}
                        onChange={(e) => setFormData({ ...formData, compliance_tags: e.target.value })}
                        placeholder="HIPAA, SOC2, GDPR (comma-separated)"
                        className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent"
                      />
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Cost / 1K Input Tokens ($)</label>
                        <input
                          type="number"
                          min="0"
                          step="0.001"
                          value={formData.cost_per_1k_input_tokens}
                          onChange={(e) => setFormData({ ...formData, cost_per_1k_input_tokens: e.target.value })}
                          placeholder="0.005"
                          className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent"
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Cost / 1K Output Tokens ($)</label>
                        <input
                          type="number"
                          min="0"
                          step="0.001"
                          value={formData.cost_per_1k_output_tokens}
                          onChange={(e) => setFormData({ ...formData, cost_per_1k_output_tokens: e.target.value })}
                          placeholder="0.015"
                          className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent"
                        />
                      </div>
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Latency SLA (ms)</label>
                        <input
                          type="number"
                          min="0"
                          value={formData.latency_sla_ms}
                          onChange={(e) => setFormData({ ...formData, latency_sla_ms: e.target.value })}
                          placeholder="30000"
                          className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent"
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Rate Limit (RPM)</label>
                        <input
                          type="number"
                          min="0"
                          value={formData.rate_limit_rpm}
                          onChange={(e) => setFormData({ ...formData, rate_limit_rpm: e.target.value })}
                          placeholder="60"
                          className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent"
                        />
                      </div>
                    </div>
                  </div>
                )}
              </div>

              <div className="flex items-center justify-end gap-2 pt-2">
                <button
                  type="button"
                  onClick={() => { setModalOpen(false); setEditingModel(null); }}
                  className="px-4 py-2 text-sm text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-700 rounded-lg transition-colors"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={submitting}
                  className="flex items-center gap-1.5 px-4 py-2 bg-teal-600 hover:bg-teal-700 disabled:bg-teal-400 text-white text-sm font-medium rounded-lg transition-colors"
                >
                  {submitting && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                  {editingModel ? "Save Changes" : "Add Model"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
