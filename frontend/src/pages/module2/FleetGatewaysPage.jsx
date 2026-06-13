import { GatewayKeyPanel } from "../../components/GatewayKeyPanel";
import { PageHeader } from "../../components/module2/PageHeader";

export function FleetGatewaysPage() {
  return (
    <div>
      <PageHeader title="Gateway Key Management" subtitle="API keys for Module 1 data plane access" />
      <GatewayKeyPanel />
    </div>
  );
}
