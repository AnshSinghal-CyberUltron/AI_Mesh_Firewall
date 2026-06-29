import { Gauge } from "lucide-react";
import { SectionCard } from "./primitives/SectionCard";
import { FieldRow } from "./primitives/FieldRow";
import { ConfigSwitch } from "./primitives/ConfigSwitch";
import { NumberStepper } from "./primitives/NumberStepper";

export function RateLimitingCard({ config, onPatch, index }) {
  return (
    <SectionCard id="rate-limiting" title="Rate Limiting & Throttling" icon={Gauge} index={index}>
      <div className="space-y-3">
        <FieldRow
          label="Rate Limiting Enabled"
          hint="Protect against abuse and DDoS"
          control={
            <ConfigSwitch
              checked={config.rateLimitEnabled}
              onCheckedChange={(v) => onPatch("rateLimitEnabled", v)}
              label="Rate Limiting Enabled"
            />
          }
        />
        <FieldRow
          label="Requests Per Minute"
          hint="Maximum requests allowed globally per minute"
          control={
            <NumberStepper
              value={config.requestsPerMinute}
              onChange={(v) => onPatch("requestsPerMinute", v)}
              min={1}
              max={10000}
              unit="req/min"
              step={100}
              width="w-44"
            />
          }
        />
        <FieldRow
          label="Burst Limit"
          hint="Maximum requests in a short burst"
          control={
            <NumberStepper
              value={config.burstLimit}
              onChange={(v) => onPatch("burstLimit", v)}
              min={1}
              max={1000}
              unit="requests"
              step={10}
              width="w-44"
            />
          }
        />
      </div>
    </SectionCard>
  );
}
