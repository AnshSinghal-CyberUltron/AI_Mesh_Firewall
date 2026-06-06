import { BellRing } from "lucide-react";
import { SectionCard } from "./primitives/SectionCard";
import { FieldRow } from "./primitives/FieldRow";
import { ConfigSwitch } from "./primitives/ConfigSwitch";
import { NumberStepper } from "./primitives/NumberStepper";
import { EmailChipInput } from "./primitives/EmailChipInput";

export function AlertingCard({ config, onPatch, index }) {
  return (
    <SectionCard id="alerting" title="Security Alerting & Notifications" icon={BellRing} index={index}>
      <div className="space-y-3">
        <FieldRow
          label="Alerting Enabled"
          hint="Send alerts for security events"
          control={
            <ConfigSwitch
              checked={config.alertingEnabled}
              onCheckedChange={(v) => onPatch("alertingEnabled", v)}
              label="Alerting Enabled"
            />
          }
        />
        <FieldRow
          label="Critical Alert Threshold"
          hint="Risk score to trigger critical alerts (0-100)"
          control={
            <NumberStepper
              value={config.criticalAlertThreshold}
              onChange={(v) => onPatch("criticalAlertThreshold", v)}
              min={0}
              max={100}
              unit="score"
              width="w-40"
            />
          }
        />
        <FieldRow
          align="stack"
          label="Alert Recipients"
          hint="Email addresses for security alerts"
          control={
            <EmailChipInput
              value={config.alertRecipients}
              onChange={(v) => onPatch("alertRecipients", v)}
            />
          }
        />
      </div>
    </SectionCard>
  );
}
