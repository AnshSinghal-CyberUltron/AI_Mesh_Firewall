import { Database } from "lucide-react";
import { SectionCard } from "./primitives/SectionCard";
import { FieldRow } from "./primitives/FieldRow";
import { ConfigSwitch } from "./primitives/ConfigSwitch";
import { NumberStepper } from "./primitives/NumberStepper";
import { ThresholdSlider } from "./primitives/ThresholdSlider";

export function RagSecurityCard({ config, onPatch, index }) {
  return (
    <SectionCard id="rag-security" title="RAG Security & Vector DB Protection" icon={Database} index={index}>
      <div className="space-y-3">
        <FieldRow
          label="RAG Enabled"
          hint="Enable Retrieval-Augmented Generation"
          control={
            <ConfigSwitch
              checked={config.ragEnabled}
              onCheckedChange={(v) => onPatch("ragEnabled", v)}
              label="RAG Enabled"
            />
          }
        />
        <FieldRow
          label="Vector DB Isolation"
          hint="Enforce tenant isolation in vector databases"
          control={
            <ConfigSwitch
              checked={config.vectorDbIsolation}
              onCheckedChange={(v) => onPatch("vectorDbIsolation", v)}
              label="Vector DB Isolation"
            />
          }
        />
        <FieldRow
          label="Max RAG Documents"
          hint="Maximum documents to retrieve"
          control={
            <NumberStepper
              value={config.ragMaxDocuments}
              onChange={(v) => onPatch("ragMaxDocuments", v)}
              min={1}
              max={20}
              unit="documents"
              width="w-44"
            />
          }
        />
        <FieldRow
          align="stack"
          label="Relevance Threshold"
          hint="Minimum similarity score for retrieval (0-1)"
          control={
            <ThresholdSlider
              value={config.ragRelevanceThreshold}
              onChange={(v) => onPatch("ragRelevanceThreshold", v)}
              ticks={[
                { value: 0, label: "0.00" },
                { value: 0.5, label: "0.50" },
                { value: 1, label: "1.00" },
              ]}
            />
          }
        />
      </div>
    </SectionCard>
  );
}
