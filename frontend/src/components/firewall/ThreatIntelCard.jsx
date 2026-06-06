import { Radio } from "lucide-react";
import { SectionCard } from "./primitives/SectionCard";
import { FieldRow } from "./primitives/FieldRow";
import { ConfigSwitch } from "./primitives/ConfigSwitch";
import { NumberStepper } from "./primitives/NumberStepper";

export function ThreatIntelCard({ config, onPatch, index }) {
  return (
    <SectionCard id="threat-intel" title="Threat Intelligence Integration" icon={Radio} index={index}>
      <div className="space-y-3">
        <FieldRow
          label="Threat Intelligence Enabled"
          hint="Integrate with threat intelligence feeds"
          control={
            <ConfigSwitch
              checked={config.threatIntelEnabled}
              onCheckedChange={(v) => onPatch("threatIntelEnabled", v)}
              label="Threat Intelligence Enabled"
            />
          }
        />
        <FieldRow
          label="Auto-Block Threats"
          hint="Automatically block known threat actors"
          control={
            <ConfigSwitch
              checked={config.autoBlockThreats}
              onCheckedChange={(v) => onPatch("autoBlockThreats", v)}
              label="Auto-Block Threats"
            />
          }
        />
        <FieldRow
          label="Threat Score Threshold"
          hint="Minimum score to trigger blocking (0-100)"
          control={
            <NumberStepper
              value={config.threatScoreThreshold}
              onChange={(v) => onPatch("threatScoreThreshold", v)}
              min={0}
              max={100}
              unit="score"
              width="w-40"
            />
          }
        />
      </div>
    </SectionCard>
  );
}
