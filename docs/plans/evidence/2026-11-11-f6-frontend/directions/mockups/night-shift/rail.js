// Renders the Night Shift icon rail. data-active on #rail picks the current item.
document.addEventListener("DOMContentLoaded", () => {
  const rail = document.getElementById("rail");
  if (!rail) return;
  const P = (d) => `<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}</svg>`;
  const items = [
    ["overview", "Overview", P('<rect x="3" y="3" width="6" height="6" rx="1"/><rect x="11" y="3" width="6" height="6" rx="1"/><rect x="3" y="11" width="6" height="6" rx="1"/><rect x="11" y="11" width="6" height="6" rx="1"/>'), true],
    ["audit", "Audit", P('<path d="M5 5h10M5 10h10M5 15h6"/>'), true],
    ["policies", "Policies", P('<path d="M10 2.5l6 2.5v4.5c0 4-2.7 6.6-6 8-3.3-1.4-6-4-6-8V5z"/><path d="M7.5 10l2 2 3.5-3.5"/>'), true],
    ["keys", "Gateway", P('<circle cx="7" cy="12" r="3.5"/><path d="M9.5 9.5L16 3M13 6l2 2"/>'), false],
    ["routing", "Routing", P('<circle cx="5" cy="5" r="2"/><circle cx="5" cy="15" r="2"/><circle cx="15" cy="10" r="2"/><path d="M5 7v6M7 5c5 0 4 5 6 5"/>'), false],
    ["kill", "Kill switch", P('<path d="M10 3v7"/><path d="M6 5.5a6 6 0 108 0"/>'), true],
    ["mcp", "MCP", P('<path d="M7 3v4M13 3v4M5 7h10v3a5 5 0 01-10 0zM10 15v2.5"/>'), false],
    ["rag", "RAG", P('<ellipse cx="10" cy="5" rx="6" ry="2.5"/><path d="M4 5v10c0 1.4 2.7 2.5 6 2.5s6-1.1 6-2.5V5M4 10c0 1.4 2.7 2.5 6 2.5s6-1.1 6-2.5"/>'), false],
    ["output", "Output", P('<path d="M3 4h14l-5 6v5l-4 2v-7z"/>'), false],
    ["sim", "Simulator", P('<path d="M8 3h4M9 3v5l-4.5 7.5A1.5 1.5 0 005.8 18h8.4a1.5 1.5 0 001.3-2.5L11 8V3"/>'), false],
  ];
  const active = rail.dataset.active;
  const logo = `<svg class="logo" viewBox="0 0 32 32" aria-hidden="true"><rect width="32" height="32" rx="8" fill="var(--accent)"/><path d="M8 11h16M8 16h9M8 21h16" stroke="var(--on-accent)" stroke-width="2.6" stroke-linecap="round"/><circle cx="22" cy="16" r="2.4" fill="var(--on-accent)"/></svg>`;
  const more = `<a href="#" class="mob">${P('<circle cx="5" cy="10" r="1.3"/><circle cx="10" cy="10" r="1.3"/><circle cx="15" cy="10" r="1.3"/>')}More</a>`;
  rail.innerHTML = logo + items.map(([id, label, icon, onMobile]) =>
    `<a href="#" class="${id === active ? "on" : ""} ${onMobile ? "" : "desk"}" ${id === active ? 'aria-current="page"' : ""}>${icon}${label}</a>`).join("") +
    `<span class="sp"></span><a href="#" class="desk">${P('<circle cx="10" cy="10" r="2.5"/><path d="M10 2.5v2M10 15.5v2M2.5 10h2M15.5 10h2M4.7 4.7l1.4 1.4M13.9 13.9l1.4 1.4M4.7 15.3l1.4-1.4M13.9 6.1l1.4-1.4"/>')}Settings</a>` + more;
});
