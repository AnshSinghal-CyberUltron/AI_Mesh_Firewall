/** Stable signature for org-managed model lists — avoids redundant parent refreshes. */
export function modelListSignature(models) {
  if (!Array.isArray(models) || models.length === 0) return "";
  return models
    .map(
      (m) =>
        `${m.id ?? ""}:${m.model_name ?? ""}:${m.model_id ?? ""}:${m.provider ?? ""}:${m.is_active ? 1 : 0}:${m.api_key_set ? 1 : 0}:${m.api_key_env_var ? 1 : 0}`,
    )
    .sort()
    .join("|");
}
