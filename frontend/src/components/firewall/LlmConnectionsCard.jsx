import { Bot, Network } from "lucide-react";
import { SectionCard } from "./primitives/SectionCard";
import { ModelConnectionPanel } from "../ModelConnectionPanel";

export function LlmProviderCard({ onModelsChanged, index }) {
  return (
    <SectionCard
      id="llm-provider"
      title="LLM Router & Model Provider"
      description="Select a provider and configure API credentials"
      icon={Bot}
      index={index}
      flushBody
    >
      <ModelConnectionPanel
        showProviderForm
        showConnectionsTable={false}
        onModelsChanged={onModelsChanged}
        embedded
      />
    </SectionCard>
  );
}

export function LlmConnectionsCard({ onModelsChanged, index }) {
  return (
    <SectionCard
      id="llm-connections"
      title="LLM Model Connections"
      description="Manage LLM provider configurations and model routing"
      icon={Network}
      index={index}
      flushBody
    >
      <ModelConnectionPanel
        showProviderForm={false}
        showConnectionsTable
        onModelsChanged={onModelsChanged}
        embedded
      />
    </SectionCard>
  );
}
