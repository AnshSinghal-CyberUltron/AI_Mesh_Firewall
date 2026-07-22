/** Shared UEBA recent-prompt display helpers (single-turn + action colors). */

export const ACTION_PROMPT_STYLES = {
  block: "border-red-300 bg-red-50 text-red-900 dark:border-red-900 dark:bg-red-950/40 dark:text-red-200",
  redact: "border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200",
  allow: "border-emerald-300 bg-emerald-50 text-emerald-900 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-200",
};

export const ACTION_LABEL_STYLES = {
  block: "text-red-700 dark:text-red-300",
  redact: "text-amber-700 dark:text-amber-300",
  allow: "text-emerald-700 dark:text-emerald-300",
};

export function actionKey(action) {
  const key = String(action || "").toLowerCase();
  if (key.includes("block")) return "block";
  if (key.includes("redact")) return "redact";
  return "allow";
}

export function actionPromptStyle(action) {
  return ACTION_PROMPT_STYLES[actionKey(action)] || ACTION_PROMPT_STYLES.allow;
}

export function actionLabelStyle(action) {
  return ACTION_LABEL_STYLES[actionKey(action)] || ACTION_LABEL_STYLES.allow;
}

function rawPromptText(req) {
  const direct = String(req?.prompt_snippet || req?.intent || req?.detail || "").trim();
  if (direct) return direct;
  const lineage = req?.prompt_lineage;
  if (Array.isArray(lineage)) {
    for (const entry of lineage) {
      const text = String(entry?.prompt || entry?.text || "").trim();
      if (text) return text;
    }
  }
  return "";
}

/** Show only the user turn that triggered this enforcement event (not full chat history). */
export function eventPromptPreview(req) {
  const raw = rawPromptText(req);
  if (!raw) return "";

  const userMatches = [...raw.matchAll(/^\[user\]:\s*(.*)$/gim)];
  if (userMatches.length) {
    return userMatches[userMatches.length - 1][1].trim();
  }

  const lines = raw.split("\n").map((line) => line.trim()).filter(Boolean);
  for (let i = lines.length - 1; i >= 0; i -= 1) {
    const line = lines[i];
    if (/^\[assistant\]:/i.test(line)) continue;
    const userLine = line.match(/^\[user\]:\s*(.*)$/i);
    if (userLine) return userLine[1].trim();
    return line;
  }

  return raw;
}

export function laneLabel(lane) {
  const key = String(lane || "chat").toLowerCase();
  return key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
