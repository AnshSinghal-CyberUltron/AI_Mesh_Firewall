import { Shield } from "lucide-react";
import { SectionCard } from "./primitives/SectionCard";
import { FieldRow } from "./primitives/FieldRow";
import { ConfigSwitch } from "./primitives/ConfigSwitch";
import { Select } from "../ui/Select";

export function GeneralSettingsCard({ config, onPatch, index }) {
  return (
    <SectionCard id="general" title="General Firewall Settings" icon={Shield} index={index}>
      <div className="space-y-3">
        <FieldRow
          label="Firewall Enabled"
          hint="Master switch for all firewall enforcement"
          control={
            <ConfigSwitch
              checked={config.firewallEnabled}
              onCheckedChange={(v) => onPatch("firewallEnabled", v)}
              label="Firewall Enabled"
            />
          }
        />
        <FieldRow
          label="Enforcement Mode"
          hint="How the firewall handles policy violations"
          control={
            <Select
              value={config.enforcementMode}
              onChange={(e) => onPatch("enforcementMode", e.target.value)}
              aria-label="Enforcement Mode"
              className="w-72"
            >
              <option value="block">Block - Reject violating requests</option>
              <option value="monitor">Monitor - Log violations, allow traffic</option>
              <option value="audit">Audit - Log only, no alerts</option>
            </Select>
          }
        />
        <FieldRow
          label="Log Level"
          hint="Detail level for security logs"
          control={
            <Select
              value={config.logLevel}
              onChange={(e) => onPatch("logLevel", e.target.value)}
              aria-label="Log Level"
              className="w-72"
            >
              <option value="minimal">Minimal - Errors only</option>
              <option value="standard">Standard - Errors + warnings</option>
              <option value="detailed">Detailed - Full request/response</option>
              <option value="verbose">Verbose - Debug information</option>
            </Select>
          }
        />
      </div>
    </SectionCard>
  );
}
