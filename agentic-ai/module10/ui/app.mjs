import { signIn, finishSignIn, signOut } from "./auth.mjs";
import { consumeEvents } from "./stream.mjs";
import { detailSections } from "./details.mjs";
import { planningControl } from "./planning.mjs";
import { resilienceControl } from "./resilience.mjs";
import { responseMessage } from "./http.mjs";

const $ = id => document.getElementById(id);
let config, auth, identity, busy = false;
const planning = planningControl(api);
const resilience = resilienceControl(api);
const pendingKey = "companion.pending";
const message = (text, error = false) => { $("notice").textContent = String(text); $("notice").classList.toggle("error", error); };
function node(tag, text, className) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = String(text);
  if (className) el.className = className;
  return el;
}
function button(text, action) {
  const el = node("button", text); el.type = "button";
  el.addEventListener("click", action); return el;
}
function controls() {
  if (identity?.team === "payments") document.documentElement.dataset.team = "payments";
  else delete document.documentElement.dataset.team;
  $("demo-controls").hidden = !identity;
  if (!identity) $("demo-controls").open = false;
  $("investigate").disabled = busy || !identity;
  $("request").disabled = busy;
  $("clear").disabled = busy;
  $("example").disabled = busy;
  $("sign-in").hidden = Boolean(auth);
  $("sign-in").disabled = !config;
  $("sign-out").hidden = !auth;
  document.querySelectorAll(".result-actions button").forEach(el => { el.disabled = busy || !identity; });
}
function clearView() {
  $("history").replaceChildren(node("div", "Your next investigation will appear here.", "empty"));
}
function expire() {
  planning.stop();
  resilience.stop();
  auth = null; identity = null; clearView(); controls();
  $("identity").textContent = "Signed out";
  message("Your sign-in expired. Sign in again to check a pending request.", true);
}
async function api(path, body, timeout = 215000) {
  if (!auth || auth.expires <= Date.now()) { expire(); throw new Error("Please sign in again."); }
  const response = await fetch(config.apiUrl + path, {
    method: body === undefined ? "GET" : "POST",
    headers: { Authorization: `Bearer ${auth.accessToken}`, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(timeout),
    cache: "no-store",
  });
  if (response.status === 401) { expire(); throw new Error("Please sign in again."); }
  return response;
}
function card(question, id) {
  $("empty")?.remove();
  if (!$("history").querySelector(".request-card")) $("history").replaceChildren();
  const article = node("article", undefined, "request-card");
  const prompt = node("div", undefined, "question");
  prompt.append(node("span", "Your request", "label"), node("div", question));
  const answer = node("div", undefined, "answer");
  answer.append(node("span", "DevOps Companion", "label"));
  const status = node("p", "Connecting to your Companion…", "answer-status");
  status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite");
  const content = node("div", undefined, "answer-text");
  const actions = node("div", undefined, "result-actions");
  const feedback = node("p", "", "action-feedback");
  feedback.setAttribute("role", "status"); feedback.setAttribute("aria-live", "polite");
  answer.append(status, content, actions, feedback); article.append(prompt, answer);
  $("history").prepend(article);
  return { article, answer, status, content, actions, feedback, id };
}
function copy(text, label) {
  const el = button(label, async () => {
    try { await navigator.clipboard.writeText(text); el.textContent = "Copied"; }
    catch { message("Copy is unavailable in this browser. Select the displayed text to copy it.", true); }
  });
  return el;
}
function renderDetails(view, body) {
  view.article.querySelector("details")?.remove();
  const details = node("details"), summary = node("summary", "Request details");
  const grid = node("div", undefined, "detail-grid");
  for (const section of detailSections(body)) {
    const group = node("section", undefined, "detail-section");
    group.append(node("h3", section.title));
    for (const [key, value, rowClass] of section.rows) {
      const p = node("p", undefined, rowClass); p.append(node("strong", key + ": "), document.createTextNode(String(value))); group.append(p);
    }
    if (section.note) group.append(node("small", section.note));
    grid.append(group);
  }
  const trace = node("section", undefined, "detail-section");
  trace.append(node("h3", "Logz.io correlation"));
  if (/^[a-f0-9]{32}$/.test(body.trace_id || "")) {
    const query = `trace_id:"${body.trace_id}"`;
    trace.append(node("p", body.trace_id, "mono"), node("code", query), node("br"),
      copy(body.trace_id, "Copy trace ID"), copy(query, "Copy search"));
    const link = node("a", "Open Logz.io ↗"); link.href = config.logzioUrl;
    link.target = "_blank"; link.rel = "noopener noreferrer";
    trace.append(node("p"), link);
  } else trace.append(node("p", "Trace ID unavailable."));
  trace.append(node("small", "Search using the trace ID. A trace ID does not confirm ingestion. Open the matching completion log, then its Trace view."));
  grid.append(trace);
  const container = node("div", undefined, "detail-body"); container.append(grid);
  details.append(summary, container); view.article.append(details);
}
function complete(view, body, recovered = false) {
  const detailsOpen = view.article.querySelector("details")?.open || false;
  const success = body.status === "completed";
  view.status.textContent = success ? (recovered ? "Completed · recovered saved result" : "Investigation complete") : "Investigation failed";
  view.status.classList.toggle("failed", !success);
  view.content.textContent = String(success ? (body.review || "Completed. See request details.") : (body.error || "The investigation did not complete."));
  view.answer.querySelector(".draft")?.remove();
  if (body.recovery_plan?.text) {
    const draft = node("section", undefined, "draft");
    draft.append(node("h3", "Recovery draft"), node("div", body.recovery_plan.text, "draft-text"),
      node("p", "Human review required. No deployment or rollback was executed.", "draft-note"));
    view.answer.insertBefore(draft, view.actions);
  }
  view.actions.replaceChildren();
  if (body.artifact_id) view.actions.append(button("Reload saved result", () => recover(view)));
  else if (!success) recoveryAction(view);
  renderDetails(view, body);
  view.article.querySelector("details").open = detailsOpen;
  const pending = JSON.parse(sessionStorage.getItem(pendingKey) || "null");
  if (pending?.id === view.id && (success || body.artifact_id)) sessionStorage.removeItem(pendingKey);
}
async function recover(view) {
  if (busy) return;
  busy = true; controls();
  view.feedback.classList.remove("failed");
  view.feedback.textContent = "Loading saved result…";
  try {
    const response = await api(`/requests/${encodeURIComponent(view.id)}`);
    if (!response.ok) throw new Error(await responseMessage(response));
    const body = await response.json();
    if (["completed", "failed"].includes(body.status)) {
      complete(view, body, true);
      view.feedback.textContent = "Saved result reloaded. No new investigation was run.";
    }
    else {
      view.status.textContent = String(`Saved status: ${body.status || "unavailable"}. ${body.status === "RUNNING" ? "Check again shortly." : "No completed result is available."}`);
      view.feedback.textContent = view.status.textContent;
      if (["FAILED", "TIMED_OUT"].includes(body.status)) sessionStorage.removeItem(pendingKey);
    }
  } catch (error) {
    view.feedback.textContent = String(error.message || "Could not load the saved result. Try again.");
    view.feedback.classList.add("failed");
    message(view.feedback.textContent, true);
  }
  finally { busy = false; controls(); }
}
function recoveryAction(view) {
  view.actions.replaceChildren(button("Check request status", () => recover(view)));
}
$("example").addEventListener("click", () => { if (config) $("request").value = config.example; $("request").focus(); });
$("clear").addEventListener("click", clearView);
$("sign-in").addEventListener("click", () => signIn(config).catch(e => message(e.message, true)));
$("sign-out").addEventListener("click", () => { planning.stop(); resilience.stop(); auth = null; identity = null; clearView(); controls(); signOut(config); });
$("request-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (busy || !identity) return;
  if (!auth || auth.expires <= Date.now()) { expire(); return; }
  const question = $("request").value.trim();
  if (!question) { message("Enter a deployment question first.", true); return; }
  const id = crypto.randomUUID();
  const principal = identity.principal;
  const view = card(question, id);
  sessionStorage.setItem(pendingKey, JSON.stringify({ id, principal }));
  busy = true; controls(); message("Investigation in progress. You can inspect the details when it completes.");
  let recoveryNeeded = true;
  try {
    const response = await api("/invoke", { change_summary: question, request_id: id });
    if (response.status === 202) {
      view.status.textContent = "This request already exists. Check its saved status."; recoveryAction(view); return;
    }
    if (!response.ok) {
      if (response.status < 500 && response.status !== 409) {
        recoveryNeeded = false; sessionStorage.removeItem(pendingKey);
      }
      throw new Error(await responseMessage(response));
    }
    if (!response.headers.get("content-type")?.includes("text/event-stream")) throw new Error("The service returned an unexpected response. Check request status.");
    const terminal = await consumeEvents(response.body, ({ event: name }) => {
      if (name === "accepted") view.status.textContent = "Request accepted · investigation running";
      else if (name === "heartbeat") view.status.textContent = "Still connected · waiting for the investigation";
      else if (name === "progress") view.status.textContent = "Investigation in progress";
    });
    if (identity?.principal !== principal) return;
    const body = { ...terminal.data, request_id: id };
    if (terminal.event === "error") body.status = "failed";
    complete(view, body);
    message(body.status === "completed" ? "Ready for another independent investigation." : "The investigation did not complete.", body.status !== "completed");
  } catch (error) {
    view.status.textContent = String(error.message || "Connection interrupted. The investigation may still be running.");
    view.status.classList.add("failed");
    if (recoveryNeeded) {
      recoveryAction(view);
      message("No new investigation was submitted automatically. Check the existing request before retrying.", true);
    } else message("The request was not admitted. Resolve the issue above before trying again.", true);
  } finally { busy = false; controls(); }
});

async function initialize() {
  const response = await fetch("config.json", { cache: "no-store" });
  if (!response.ok) throw new Error("Application configuration is unavailable.");
  config = await response.json();
  auth = await finishSignIn(config);
  if (!auth) { message("Sign in to investigate a deployment with your team’s capabilities."); controls(); return; }
  const me = await api("/me");
  if (!me.ok) throw new Error(await responseMessage(me));
  identity = await me.json();
  $("identity").textContent = String(`${identity.team} team`);
  message("Signed in. Your team’s access and capabilities are applied automatically.");
  controls();
  planning.start();
  resilience.start();
  const pending = JSON.parse(sessionStorage.getItem(pendingKey) || "null");
  if (pending?.principal === identity.principal && /^[A-Za-z0-9_-]{1,128}$/.test(pending.id)) {
    const view = card("Previously submitted investigation", pending.id);
    view.status.textContent = "A previous request may have completed. Check its saved result."; recoveryAction(view);
  } else sessionStorage.removeItem(pendingKey);
}
initialize().catch(error => { message(error.message || "Application could not start.", true); controls(); });
