/* Small demo adaptations to the bundled Portkey log inspector. */
const escapeText = (value) => String(value).replace(/[&<>"]/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;",
}[c]));
// The hidden getting-started examples do not need remote visual libraries.
window.lucide = { createIcons() {} };
window.hljs = { highlight: (code) => ({ value: escapeText(code) }) };
window.confetti = () => {};

async function ensureDemoConsoleAuth() {
  const token = new URLSearchParams(location.hash.slice(1)).get("token");
  history.replaceState(null, "", location.pathname);
  if (document.readyState === "loading") {
    await new Promise((done) => document.addEventListener("DOMContentLoaded", done, { once: true }));
  }
  if (token) await unlockUiWithToken(token);
  await ensureAdminAuth();
}

document.addEventListener("DOMContentLoaded", () => {
  const style = document.createElement("style");
  style.textContent = `
    #main-tab-button,.github-button,.header-links { display:none !important }
    .main-content { max-width:1400px }
    .card.logs-card { max-width:1200px; width:100%; box-sizing:border-box }
    .logs-table-container { max-width:none; overflow-x:auto }
    .logs-table td { vertical-align:top }
    .logs-table .fallback { background:#fff8e5 }
    .logs-table .route { white-space:normal; min-width:160px }
    #logDetailsContent pre { white-space:pre-wrap; overflow-wrap:anywhere; font-size:12px; max-height:340px; overflow:auto; background:#f5f6f8; padding:12px }
    .module10-note { color:#687080; margin:8px 0 18px; font-size:14px }
    .model-attempt { padding:8px 12px; margin:6px 0; background:#f5f6f8; border-radius:6px }
    @media (max-width:800px) {
      .main-content { padding:8px }
      .card.logs-card { padding:12px }
      .logs-table th,.logs-table td { padding:8px; font-size:12px }
      .logs-table th:first-child,.logs-table td:first-child,
      .logs-table th:nth-child(5),.logs-table td:nth-child(5) { display:none }
      .logs-table .route { min-width:120px }
      .modal-content { width:100%; max-width:none; box-sizing:border-box; padding:20px }
    }
  `;
  document.head.append(style);
  document.querySelector(".logs-card h2").textContent = "DevOps Companion · Model requests";
  const note = document.createElement("p");
  note.className = "module10-note";
  note.textContent = "Run an investigation. This view shows requests processed by this Portkey gateway; simulated interruptions are labeled. Click View Details to inspect the evidence.";
  document.querySelector(".logs-card h2").after(note);
  const headings = ["Time", "Agent", "Model route", "Status", "Duration", "Details"];
  document.querySelectorAll(".logs-table th").forEach((th, i) => { th.textContent = headings[i]; });
  const names = { "devops-companion": "DevOps Companion", "recovery-planner": "Recovery Planner" };
  const modelNames = { "gpt-5.6-luna": "GPT-5.6 Luna", "gpt-5.6-terra": "GPT-5.6 Terra" };
  const modelName = (name) => {
    const shortName = String(name).replace(/^(us|global)\.openai\./, "");
    return modelNames[shortName] || shortName;
  };
  const seen = new Set();
  const statuses = (attempts) => attempts.map((a) =>
    `${a.status_code}${a.source === "injected" ? " (simulated)" : ""}`,
  ).join(" → ");

  // Render model content as text. The upstream HTML template's detail renderer
  // interpolates JSON into innerHTML, which is unsuitable for agent output.
  window.addLogEntry = (time, method, endpoint, status, duration, options) => {
    const info = options[0].module10;
    if (seen.has(info.call_id)) return;
    seen.add(info.call_id);
    const tr = document.createElement("tr");
    if (info.attempts.length > 1) tr.className = "fallback";
    const values = [time, names[info.agent] || info.agent,
      info.attempts.map((a) => modelName(a.model)).join(" → "),
      statuses(info.attempts) || String(status), `${duration} ms`];
    for (const [i, value] of values.entries()) {
      const td = document.createElement("td");
      td.textContent = value;
      if (i === 2) td.className = "route";
      tr.append(td);
    }
    const td = document.createElement("td");
    const button = document.createElement("button");
    button.className = "btn-view-details";
    button.textContent = "View Details";
    button.onclick = () => showLogDetails(time, method, endpoint, status, duration, options);
    td.append(button);
    tr.append(td);
    const tbody = document.getElementById("logsTableBody");
    tbody.querySelector(".loading-row")?.remove();
    tbody.prepend(tr);
    while (tbody.children.length > 100) tbody.lastChild.remove();
  };

  window.showLogDetails = (time, method, endpoint, status, duration, options) => {
    const { requestParams, response, module10: info } = options[0];
    const content = document.getElementById("logDetailsContent");
    content.replaceChildren();
    const add = (tag, text, className = "") => {
      const element = document.createElement(tag);
      element.textContent = text;
      element.className = className;
      content.append(element);
    };
    add("h3", names[info.agent] || info.agent);
    add("p", `${method} ${endpoint} · HTTP ${status} · ${duration} ms · ${time}`);
    add("h3", "Observed model attempts");
    for (const attempt of info.attempts) {
      const source = attempt.source === "injected" ? "simulated; no AWS request" : attempt.source;
      add("p", `${modelName(attempt.model)} → ${attempt.status_code} · ${source} · ${attempt.duration_ms} ms`, "model-attempt");
    }
    add("h3", "Request and tool evidence");
    add("pre", JSON.stringify(requestParams, null, 2));
    add("h3", "Response");
    add("pre", JSON.stringify(response, null, 2));
    document.getElementById("logDetailsModal").style.display = "block";
  };
});
