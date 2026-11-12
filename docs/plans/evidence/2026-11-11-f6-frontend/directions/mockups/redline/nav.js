// Renders the Redline console navigation. data-active on #nav picks the item.
document.addEventListener("DOMContentLoaded", () => {
  const nav = document.getElementById("nav");
  if (!nav) return;
  const a = nav.dataset.active;
  const item = (id, label, n) => `<a href="#" class="${id === a ? "on" : ""}" ${id === a ? 'aria-current="page"' : ""}><span class="n">${n || ""}</span>${label}</a>`;
  nav.innerHTML = `<a class="mark" href="#" style="display:inline-flex"><i aria-hidden="true"></i><b>ZeroShield</b></a>` +
    item("overview", "Overview") + item("audit", "Audit log") +
    `<h4>Checkpoints</h4>` +
    item("keys", "Gateway and keys", "1.1") + item("policies", "Policies", "1.2") + item("rag", "RAG and vectors", "1.3") +
    item("mcp", "MCP tools", "1.4") + item("routing", "Model routing", "1.5") + item("kill", "Kill switch", "1.6") + item("output", "Output guardrails", "1.7") +
    `<h4>Tools</h4>` + item("sim", "Attack simulator") + item("conn", "Connections") +
    `<div class="foot"><b>Default organization</b>Gateway and control plane reachable</div>`;
});
