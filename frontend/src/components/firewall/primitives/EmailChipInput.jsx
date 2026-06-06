import { useState } from "react";
import { motion } from "motion/react";
import { X } from "lucide-react";

function normalizeEmails(value) {
  if (Array.isArray(value)) return value.filter(Boolean);
  if (!value) return [];
  return String(value)
    .split(/[,;\s]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

export function EmailChipInput({ value, onChange }) {
  const [draft, setDraft] = useState("");
  const emails = normalizeEmails(value);

  const emit = (next) => {
    onChange(next.join(", "));
  };

  const add = () => {
    const v = draft.trim();
    if (v && !emails.includes(v)) emit([...emails, v]);
    setDraft("");
  };

  return (
    <div className="space-y-2">
      <div className="flex min-h-11 flex-wrap gap-2 rounded-md border border-input bg-background p-2">
        {emails.map((email) => (
          <motion.span
            key={email}
            initial={{ scale: 0.8, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            className="inline-flex items-center gap-1.5 rounded-md bg-accent px-2 py-1 font-mono text-xs text-accent-foreground"
          >
            {email}
            <button
              type="button"
              onClick={() => emit(emails.filter((e) => e !== email))}
              className="rounded-sm text-accent-foreground/60 hover:text-accent-foreground"
            >
              <X className="h-3 w-3" />
            </button>
          </motion.span>
        ))}
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === ",") {
              e.preventDefault();
              add();
            }
          }}
          onBlur={add}
          placeholder="Add email and press Enter"
          className="min-w-40 flex-1 bg-transparent px-1 text-sm outline-none placeholder:text-muted-foreground"
        />
      </div>
    </div>
  );
}
