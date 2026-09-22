// Keep live shared state separate from the immutable evidence on request cards.
export function planningControl(api) {
  return sharedFlagControl(api);
}

export function sharedFlagControl(api, {
  id = "planning", label = "Shared flag", validate = () => true,
  describe = body => `${label}: ${body.enabled ? "On" : "Off"}`,
  action = body => body.enabled ? "Turn off" : "Turn on",
  desired = body => !body.enabled,
  refreshed = `${label} is up to date. Refreshes automatically.`,
  changed = body => `${label} turned ${body.enabled ? "on" : "off"}. Refreshes automatically.`,
} = {}) {
  const panel = document.getElementById(`${id}-panel`);
  const status = document.getElementById(`${id}-state`);
  const feedback = document.getElementById(`${id}-feedback`);
  const toggle = document.getElementById(`${id}-toggle`);
  let active = false, generation = 0, timer, state, editing = false;
  const schedule = () => { if (active) timer = setTimeout(refresh, 5000); };
  function render() {
    toggle.hidden = !state?.can_edit;
    toggle.disabled = editing || !state;
    toggle.textContent = editing ? "Updating…" : state ? action(state) : "Checking…";
  }
  function accept(body) {
    if (typeof body.enabled !== "boolean" || typeof body.revision !== "string" || typeof body.can_edit !== "boolean" || !validate(body))
      throw new Error("Invalid flag status");
    state = body;
    status.textContent = describe(state);
    status.classList.remove("failed");
    render();
  }
  function unavailable(text) {
    state = undefined;
    status.textContent = `${label}: unavailable`;
    status.classList.add("failed");
    feedback.textContent = text;
    render();
  }
  async function refresh() {
    clearTimeout(timer);
    if (!active || editing) return;
    const current = ++generation;
    try {
      const response = await api(`/${id}`, undefined, 10000);
      if (!response.ok) throw new Error("Status unavailable");
      const body = await response.json();
      if (!active || current !== generation) return;
      accept(body);
      feedback.textContent = refreshed;
    } catch {
      if (!active || current !== generation) return;
      unavailable("Could not check the current flag. Retrying automatically.");
    }
    if (active && current === generation) schedule();
  }
  toggle.addEventListener("click", async () => {
    if (!active || editing || !state?.can_edit) return;
    clearTimeout(timer);
    const current = ++generation, enabled = desired(state), revision = state.revision;
    editing = true; render();
    feedback.textContent = `Updating ${label.toLowerCase()}…`;
    try {
      const response = await api(`/${id}`, { enabled, revision }, 10000);
      if (!active || current !== generation) return;
      if (response.status === 409) {
        unavailable("The flag changed elsewhere. Checking the latest state; your change was not applied.");
        editing = false;
        await refresh();
        if (active) feedback.textContent = "The flag changed elsewhere. Review the current state before trying again.";
        return;
      }
      if (!response.ok) throw new Error("Update failed");
      const body = await response.json();
      if (!active || current !== generation) return;
      accept(body);
      feedback.textContent = changed(body);
    } catch {
      if (!active || current !== generation) return;
      unavailable("Could not confirm the change. Checking again before allowing another change.");
    } finally {
      if (active && current === generation) { editing = false; render(); schedule(); }
    }
  });
  return {
    start() { active = true; panel.hidden = false; refresh(); },
    stop() { active = false; generation++; clearTimeout(timer); state = undefined; editing = false; panel.hidden = true; render(); },
  };
}
