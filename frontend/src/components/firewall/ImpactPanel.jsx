import { Activity, BarChart3, FileCheck, Check, Zap } from "lucide-react";
import { motion } from "motion/react";
import { SectionCard } from "./primitives/SectionCard";

export function ImpactPanel({ config, index }) {
  const items = [
    {
      icon: Zap,
      title: "Enforcement Impact",
      sub: "Affects request blocking, rate limiting, and policy decisions",
      bullets: [
        config.enforcementMode === "block" ? "Blocking mode active" : `${config.enforcementMode} mode`,
        `Rate limit: ${config.requestsPerMinute} req/min`,
        config.contentFilteringEnabled ? "Content filtering enabled" : "Content filtering disabled",
      ],
    },
    {
      icon: BarChart3,
      title: "Dashboard Impact",
      sub: "Updates metrics, graphs, and real-time analytics",
      bullets: ["Real-time threat detection charts", "Risk distribution graphs", "Activity feed and logs"],
    },
    {
      icon: FileCheck,
      title: "Compliance Impact",
      sub: "Affects audit trails and regulatory reporting",
      bullets: [
        config.complianceFrameworks.length > 0
          ? `${config.complianceFrameworks.length} framework(s) in reporting scope`
          : "No framework filter (detection tags still apply)",
        `${config.retentionDays}-day log retention`,
        config.auditLoggingEnabled ? "Full audit trail" : "Limited logging",
      ],
    },
  ];

  return (
    <SectionCard
      id="impact"
      title="Configuration Impact & Audit Trail"
      description="Changes to this configuration affect real-time firewall enforcement, analytics dashboards, and compliance reporting"
      icon={Activity}
      index={index}
    >
      <div className="grid gap-3 md:grid-cols-3">
        {items.map((it) => {
          const Icon = it.icon;
          return (
            <motion.div
              key={it.title}
              whileHover={{ y: -2 }}
              transition={{ type: "spring", stiffness: 400, damping: 28 }}
              className="rounded-lg border border-border bg-surface-2/60 p-4"
            >
              <div className="flex items-center gap-2">
                <div className="flex h-7 w-7 items-center justify-center rounded-md bg-primary/10 text-primary">
                  <Icon className="h-4 w-4" />
                </div>
                <p className="text-sm font-semibold text-foreground">{it.title}</p>
              </div>
              <p className="mt-2 text-xs text-muted-foreground">{it.sub}</p>
              <ul className="mt-3 space-y-1.5">
                {it.bullets.map((b) => (
                  <li key={b} className="flex items-center gap-2 text-xs text-foreground">
                    <Check className="h-3 w-3 text-primary" />
                    <span className="font-mono">{b}</span>
                  </li>
                ))}
              </ul>
            </motion.div>
          );
        })}
      </div>
    </SectionCard>
  );
}
