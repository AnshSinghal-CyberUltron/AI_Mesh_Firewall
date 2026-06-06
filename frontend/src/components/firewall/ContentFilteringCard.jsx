import { Filter } from "lucide-react";
import { SectionCard } from "./primitives/SectionCard";
import { FieldRow } from "./primitives/FieldRow";
import { ConfigSwitch } from "./primitives/ConfigSwitch";
import { ThresholdSlider } from "./primitives/ThresholdSlider";
import { Textarea } from "../ui/Textarea";

export function ContentFilteringCard({ config, onPatch, index }) {
  const charCount = config.blockedKeywords?.length ?? 0;

  return (
    <SectionCard id="content-filtering" title="Content Filtering & Detection" icon={Filter} index={index}>
      <div className="space-y-3">
        <FieldRow
          label="Content Filtering Enabled"
          hint="Scan and filter sensitive content"
          control={
            <ConfigSwitch
              checked={config.contentFilteringEnabled}
              onCheckedChange={(v) => onPatch("contentFilteringEnabled", v)}
              label="Content Filtering Enabled"
            />
          }
        />
        <FieldRow
          label="PII Detection"
          hint="Detect and redact personally identifiable information"
          control={
            <ConfigSwitch
              checked={config.piiDetectionEnabled}
              onCheckedChange={(v) => onPatch("piiDetectionEnabled", v)}
              label="PII Detection"
            />
          }
        />
        <FieldRow
          align="stack"
          label="Toxicity Threshold"
          hint="Minimum score to flag toxic content (0-1)"
          control={
            <ThresholdSlider
              value={config.toxicityThreshold}
              onChange={(v) => onPatch("toxicityThreshold", v)}
              ticks={[
                { value: 0, label: "0.00" },
                { value: 0.5, label: "0.50" },
                { value: 1, label: "1.00" },
              ]}
            />
          }
        />
        <FieldRow
          align="stack"
          label={
            <div className="flex items-center justify-between gap-3">
              <span>Blocked Keywords</span>
              <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                {charCount} chars
              </span>
            </div>
          }
          hint="Comma-separated list of keywords to block"
          control={
            <Textarea
              value={config.blockedKeywords}
              onChange={(e) => onPatch("blockedKeywords", e.target.value)}
              placeholder="password, secret, api_key, token"
              rows={2}
              maxLength={512}
              className="font-mono text-xs"
            />
          }
        />
      </div>
    </SectionCard>
  );
}
