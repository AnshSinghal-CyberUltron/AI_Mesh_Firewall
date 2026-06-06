import { motion } from "motion/react";
import { Loader2 } from "lucide-react";
import { Button } from "../../ui/Button";

export function SaveBar({ isDirty, isSaving, onReset, onSave }) {
  return (
    <motion.div
      initial={false}
      animate={{ opacity: isDirty ? 1 : 0.6 }}
      className="flex flex-wrap items-center justify-start gap-2 sm:justify-end sm:gap-3"
    >
      {isDirty && (
        <motion.div
          initial={{ opacity: 0, x: -8 }}
          animate={{ opacity: 1, x: 0 }}
          className="flex items-center gap-2 rounded-full border border-warn/30 bg-warn/10 px-3 py-1 text-xs font-medium text-warn-foreground"
        >
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-warn opacity-75" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-warn" />
          </span>
          Unsaved changes
        </motion.div>
      )}
      <Button
        variant="ghost"
        size="sm"
        onClick={onReset}
        disabled={!isDirty || isSaving}
        className="text-foreground hover:bg-accent hover:text-accent-foreground"
      >
        Reset
      </Button>
      <Button
        size="sm"
        onClick={onSave}
        disabled={!isDirty || isSaving}
        className="bg-primary text-primary-foreground shadow-glow hover:opacity-90"
      >
        {isSaving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
        Save Configuration
      </Button>
    </motion.div>
  );
}
