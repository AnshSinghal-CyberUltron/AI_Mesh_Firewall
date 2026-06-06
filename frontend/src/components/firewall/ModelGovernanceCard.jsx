import { Scale, Layers } from "lucide-react";
import { SectionCard } from "./primitives/SectionCard";
import { FieldRow } from "./primitives/FieldRow";
import { ConfigSwitch } from "./primitives/ConfigSwitch";
import { ModelGovernanceFields } from "../ModelGovernanceFields";

export function ModelGovernanceCard({ config, connectedModels, isSaving, onPatch, index }) {
  const hasModels = connectedModels?.length > 0;

  return (
    <SectionCard id="model-governance" title="Model Governance & Routing" icon={Scale} index={index}>
      <div className="space-y-3">
        <FieldRow
          label="Model Isolation Enabled"
          hint="Enforce strict model boundaries"
          control={
            <ConfigSwitch
              checked={config.modelIsolationEnabled}
              onCheckedChange={(v) => onPatch("modelIsolationEnabled", v)}
              label="Model Isolation Enabled"
            />
          }
        />
        {!hasModels ? (
          <div className="rounded-lg border border-dashed border-border bg-surface-2/40 p-5 text-center">
            <div className="mx-auto mb-2 flex h-9 w-9 items-center justify-center rounded-full bg-muted">
              <Layers className="h-4 w-4 text-muted-foreground" />
            </div>
            <p className="text-sm font-medium text-foreground">No models connected yet</p>
            <p className="mt-1 text-xs text-muted-foreground">
              Add provider credentials under Model Connection below. Allowed and default models are limited to models
              your organization has connected.
            </p>
          </div>
        ) : (
          <ModelGovernanceFields
            connectedModels={connectedModels}
            allowedModelsValue={config.allowedModels}
            defaultModel={config.defaultModel}
            modelIsolationEnabled={config.modelIsolationEnabled}
            disabled={isSaving}
            onAllowedChange={(v) => onPatch("allowedModels", v)}
            onDefaultChange={(v) => onPatch("defaultModel", v)}
          />
        )}
      </div>
    </SectionCard>
  );
}
