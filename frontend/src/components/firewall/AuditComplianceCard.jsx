import { FileCheck } from "lucide-react";
import { SectionCard } from "./primitives/SectionCard";
import { FieldRow } from "./primitives/FieldRow";
import { ConfigSwitch } from "./primitives/ConfigSwitch";
import { NumberStepper } from "./primitives/NumberStepper";
import { ChipMultiSelect } from "./primitives/ChipMultiSelect";
import { COMPLIANCE_OPTIONS } from "./constants";

export function AuditComplianceCard({ config, onPatch, index }) {
  return (
    <SectionCard id="audit-compliance" title="Audit Logging & Compliance" icon={FileCheck} index={index}>
      <div className="space-y-3">
        <FieldRow
          label="Audit Logging Enabled"
          hint="Comprehensive audit trail for compliance"
          control={
            <ConfigSwitch
              checked={config.auditLoggingEnabled}
              onCheckedChange={(v) => onPatch("auditLoggingEnabled", v)}
              label="Audit Logging Enabled"
            />
          }
        />
        <FieldRow
          label="Retention Days"
          hint="How long to retain audit logs"
          control={
            <NumberStepper
              value={config.retentionDays}
              onChange={(v) => onPatch("retentionDays", v)}
              min={1}
              max={365}
              unit="days"
              width="w-40"
            />
          }
        />
        <FieldRow
          align="stack"
          label="Compliance Frameworks"
          hint="Reporting scope for audit exports — does not disable detection tags on MCP events. Leave empty to still see per-event compliance tags from scans."
          control={
            <ChipMultiSelect
              options={COMPLIANCE_OPTIONS}
              value={config.complianceFrameworks}
              onChange={(v) => onPatch("complianceFrameworks", v)}
            />
          }
        />
      </div>
    </SectionCard>
  );
}
