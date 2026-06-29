/** BYOK vector provider choices — aligned with control VectorProviderConfig. */
export const DEFAULT_VECTOR_PROVIDER = "pinecone";

export const VECTOR_PROVIDERS = [
  { value: "pinecone", label: "Pinecone" },
  { value: "milvus", label: "Milvus" },
  { value: "chroma", label: "Chroma" },
  { value: "custom", label: "Custom" },
];

export const VECTOR_PROVIDER_CONFIG_FIELDS = {
  pinecone: [
    { key: "api_key", label: "API Key", placeholder: "pcsk_...", required: true, type: "password" },
    { key: "environment", label: "Environment", placeholder: "us-east-1" },
  ],
  milvus: [
    { key: "connection_url", label: "Milvus URI", placeholder: "http://localhost:19530", required: true },
    { key: "api_key", label: "Token (optional)", placeholder: "", type: "password" },
  ],
  // Chroma is now a per-org BYOK provider (not a gateway built-in). It needs a
  // reachable server URL plus an optional auth token (mapped to api_key).
  chroma: [
    { key: "connection_url", label: "Chroma Server URL", placeholder: "http://your-chroma-host:8000", required: true },
    { key: "api_key", label: "Auth Token (optional)", placeholder: "", type: "password" },
  ],
  custom: [
    { key: "connection_url", label: "Connection URL", placeholder: "https://your-vector-endpoint", required: true },
    { key: "api_key", label: "Token (optional)", placeholder: "", type: "password" },
  ],
};
