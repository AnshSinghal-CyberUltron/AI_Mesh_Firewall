import { Lock } from "lucide-react";
import { SectionCard } from "./primitives/SectionCard";
import { FieldRow } from "./primitives/FieldRow";
import { ConfigSwitch } from "./primitives/ConfigSwitch";
import { NumberStepper } from "./primitives/NumberStepper";
import { ThresholdSlider } from "./primitives/ThresholdSlider";
import { Select } from "../ui/Select";
import { Tier2ScanControl } from "./Tier2ScanControl";
import { ZEROSHIELD_TIER2_LABEL } from "../../constants/zeroshieldBrand";

export function PromptSecurityCard({ config, onPatch, index }) {
  return (
    <SectionCard id="prompt-security" title="Prompt Security & Injection Protection" icon={Lock} index={index}>
      <div className="space-y-3">
        {/*
          Tier-2 opt-in toggle (policy-driven-detection Req 3). Self-contained:
          reflects the backend tier2_enabled tri-state and PUTs the change
          immediately (partial PUT), independent of the batched Save button.
        */}
        <Tier2ScanControl />
        <FieldRow
          label="Jailbreak Detection"
          hint="Detect and block prompt injection attempts"
          control={
            <ConfigSwitch
              checked={config.jailbreakDetectionEnabled}
              onCheckedChange={(v) => onPatch("jailbreakDetectionEnabled", v)}
              label="Jailbreak Detection"
            />
          }
        />
        <FieldRow
          label="Semantic Analysis"
          hint={`Deep analysis of prompt intent (${ZEROSHIELD_TIER2_LABEL})`}
          control={
            <ConfigSwitch
              checked={config.semanticAnalysisEnabled}
              onCheckedChange={(v) => onPatch("semanticAnalysisEnabled", v)}
              label="Semantic Analysis"
            />
          }
        />
        <FieldRow
          label="ZeroShield Model Execution Mode"
          hint="Choose if the ZeroShield Model runs before or after LLM forwarding"
          control={
            <Select
              value={config.tier2ExecutionMode}
              onChange={(e) => onPatch("tier2ExecutionMode", e.target.value)}
              aria-label="ZeroShield Model Execution Mode"
              className="w-80"
            >
              <option value="sync_pre_llm">Sync Pre-LLM (recommended for strict blocking)</option>
              <option value="async_post_llm">Async Post-LLM (lower latency, post-response enforcement)</option>
            </Select>
          }
        />
        <FieldRow
          label="ZeroShield Model Stream Hold"
          hint="Allow a short pre-stream hold window in async mode for block semantics"
          control={
            <ConfigSwitch
              checked={config.tier2StreamHoldEnabled}
              onCheckedChange={(v) => onPatch("tier2StreamHoldEnabled", v)}
              label="ZeroShield Model Stream Hold"
            />
          }
        />
        <FieldRow
          label="ZeroShield Model Stream Hold Timeout"
          hint="Max hold time before stream starts when hold mode is enabled"
          control={
            <NumberStepper
              value={config.tier2StreamHoldTimeoutMs}
              onChange={(v) => onPatch("tier2StreamHoldTimeoutMs", v)}
              min={500}
              max={2000}
              unit="ms"
              step={100}
              width="w-40"
            />
          }
        />
        <FieldRow
          align="stack"
          label="Prompt Injection Threshold"
          hint="Sensitivity for injection detection (0-1)"
          control={
            <ThresholdSlider
              value={config.promptInjectionThreshold}
              onChange={(v) => onPatch("promptInjectionThreshold", v)}
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
