import { useEffect, useState } from "react";
import { motion, useReducedMotion } from "motion/react";
import { cn } from "../../../lib/utils";
import { FIREWALL_SECTIONS } from "../constants";

export function useActiveSection(sections = FIREWALL_SECTIONS) {
  const [active, setActive] = useState(sections[0]?.id ?? "");

  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => b.intersectionRatio - a.intersectionRatio);
        if (visible[0]?.target?.id) setActive(visible[0].target.id);
      },
      { rootMargin: "-30% 0px -55% 0px", threshold: [0, 0.25, 0.5, 1] },
    );

    sections.forEach((s) => {
      const el = document.getElementById(s.id);
      if (el) observer.observe(el);
    });

    return () => observer.disconnect();
  }, [sections]);

  return active;
}

export function SectionNav({ sections = FIREWALL_SECTIONS, activeId }) {
  const reduceMotion = useReducedMotion();

  const scrollTo = (id) => {
    document.getElementById(id)?.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
  };

  return (
    <nav aria-label="Configuration sections" className="sticky top-28">
      <div className="rounded-xl border border-border bg-card/60 p-2 backdrop-blur">
        <p className="px-3 pt-2 pb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          Sections
        </p>
        <ul className="space-y-0.5">
          {sections.map((s) => {
            const active = activeId === s.id;
            return (
              <li key={s.id} className="relative">
                {active && !reduceMotion && (
                  <motion.div
                    layoutId="section-nav-indicator"
                    className="absolute inset-0 rounded-md bg-accent"
                    transition={{ type: "spring", stiffness: 380, damping: 30 }}
                  />
                )}
                <button
                  type="button"
                  onClick={() => scrollTo(s.id)}
                  className={cn(
                    "relative block w-full rounded-md px-3 py-1.5 text-left text-xs font-medium transition-colors",
                    active ? "text-accent-foreground" : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  {s.label}
                </button>
              </li>
            );
          })}
        </ul>
      </div>
    </nav>
  );
}
