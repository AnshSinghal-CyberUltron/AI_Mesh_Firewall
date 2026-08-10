import { AIMeshFirewallConfig } from "../AIMeshFirewallConfig";
import { PageHeader } from "../../components/module2/PageHeader";

export function SettingsOrgPage() {
  return (
    <div>
      <PageHeader title="Organization Settings" subtitle="Firewall config, integrations, and org-wide defaults" />
      <AIMeshFirewallConfig />
    </div>
  );
}
