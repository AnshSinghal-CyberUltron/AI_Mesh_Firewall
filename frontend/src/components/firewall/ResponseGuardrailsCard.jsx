import { ShieldCheck } from "lucide-react";
import { SectionCard } from "./primitives/SectionCard";
import { FieldRow } from "./primitives/FieldRow";
import { ConfigSwitch } from "./primitives/ConfigSwitch";
import { NumberStepper } from "./primitives/NumberStepper";
import { OutputGuardrailControls } from "../OutputGuardrailControls";

export function ResponseGuardrailsCard({ config, onPatch, onGuardrailsSaved, index }) {
  return (
    <SectionCard id="response-guardrails" title="Response Guardrails & Validation" icon={ShieldCheck} index={index}>
      <div className="space-y-4">
        <FieldRow
          label="Response Filtering Enabled"
          hint="Scan and filter model responses"
          control={
            <ConfigSwitch
              checked={config.responseFilteringEnabled}
              onCheckedChange={(v) => onPatch("responseFilteringEnabled", v)}
              label="Response Filtering Enabled"
            />
          }
        />
        <FieldRow
          label="Factuality Check"
          hint="Verify response accuracy (hallucination detection)"
          control={
            <ConfigSwitch
              checked={config.factualityCheckEnabled}
              onCheckedChange={(v) => onPatch("factualityCheckEnabled", v)}
              label="Factuality Check"
            />
          }
        />
        <FieldRow
          label="Max Response Tokens"
          hint="Maximum tokens in model response"
          control={
            <NumberStepper
              value={config.maxResponseTokens}
              onChange={(v) => onPatch("maxResponseTokens", v)}
              min={100}
              max={32000}
              unit="tokens"
              step={256}
              width="w-44"
            />
          }
        />
        <div className="rounded-xl border border-border bg-surface-2/60 p-4 sm:p-5">
          <OutputGuardrailControls onSaved={onGuardrailsSaved} embedded />
        </div>
      </div>
    </SectionCard>
  );
}
