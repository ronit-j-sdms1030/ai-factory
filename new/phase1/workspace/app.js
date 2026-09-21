const actorSelect = document.getElementById("actor");
const errorEl = document.getElementById("error");
let currentId = null;
let eventController = null;

function showError(msg) {
  errorEl.hidden = !msg;
  errorEl.textContent = msg || "";
}

async function api(path, opts) {
  const token = localStorage.getItem("phase1Token");
  const options = { ...(opts || {}), headers: { ...((opts || {}).headers || {}) } };
  if (token) options.headers.Authorization = `Bearer ${token}`;
  const res = await fetch(path, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function view(name) {
  document.querySelectorAll("nav button").forEach((b) => {
    b.classList.toggle("active", b.dataset.view === name);
  });
  document.querySelectorAll("main > section").forEach((s) => {
    s.hidden = s.id !== `view-${name}`;
  });
}

document.querySelector("nav").addEventListener("click", (e) => {
  const btn = e.target.closest("button");
  if (!btn) return;
  view(btn.dataset.view);
  if (btn.dataset.view === "inbox") loadInbox();
  if (btn.dataset.view === "runs") loadRuns();
});

async function boot() {
  const dir = await api("/api/directory");
  actorSelect.innerHTML = dir.actors
    .map((a) => `<option value="${a.id}">${a.name} (${a.roles.join(", ")})</option>`)
    .join("");
}

document.getElementById("submit-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  showError("");
  const fd = new FormData(e.target);
  try {
    const run = await api("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        originator_id: actorSelect.value,
        template: fd.get("template"),
        request: fd.get("request"),
      }),
    });
    currentId = run.id;
    view("run");
    renderRun(run);
  } catch (err) {
    showError(err.message);
  }
});

document.getElementById("turn-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  showError("");
  const fd = new FormData(e.target);
  try {
    const run = await api(`/api/runs/${currentId}/turn`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: fd.get("message") }),
    });
    e.target.reset();
    renderRun(run);
  } catch (err) {
    showError(err.message);
  }
});

document.getElementById("save-brd").addEventListener("click", async () => {
  showError("");
  try {
    const run = await api(`/api/runs/${currentId}/brd`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        actor_id: actorSelect.value,
        content: document.getElementById("brd-text").value,
      }),
    });
    renderRun(run);
  } catch (err) {
    showError(err.message);
  }
});

async function decide(outcome, gate) {
  showError("");
  try {
    const reason =
      outcome === "discard" || outcome === "revise"
        ? window.prompt(`Reason for ${outcome}:`, "") || ""
        : "";
    if (outcome === "discard" && !reason.trim()) {
      throw new Error("A discard reason is required.");
    }
    const run = await api(`/api/runs/${currentId}/decide`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        actor_id: actorSelect.value,
        outcome,
        gate,
        channel: "workspace",
        reason,
      }),
    });
    renderRun(run);
  } catch (err) {
    showError(err.message);
  }
}

function bubbleText(msg) {
  try {
    const data = JSON.parse(msg.content);
    if (data.type === "question") return data.text;
    if (data.type === "scope_report") return "Scope report drafted from this conversation.";
  } catch (_) {}
  return msg.content;
}

function renderRun(run, connect = true) {
  currentId = run.id;
  document.getElementById("run-title").textContent = run.id;
  document.getElementById("run-meta").textContent =
    `${run.phase} · template ${run.template} · awaiting gate ${run.awaiting ?? "—"}` +
    (run.escalated ? " · SLA ESCALATED" : "") +
    (run.sla_due ? ` · SLA ${run.sla_due}` : "");
  document.getElementById("approver-list").innerHTML = Object.entries(
    run.named_approvers || {},
  )
    .map(
      ([gate, people]) =>
        `<li><strong>Gate ${escapeHtml(gate)}</strong>: ${people
          .map((person) => escapeHtml(person.name || person.role || "Unresolved"))
          .join(", ")}</li>`,
    )
    .join("");
  const conv = document.getElementById("conversation");
  conv.innerHTML = (run.messages || [])
    .map((m) => `<div class="bubble ${m.role}">${escapeHtml(bubbleText(m))}</div>`)
    .join("");
  const turn = document.getElementById("turn-form");
  turn.hidden = run.phase !== "intake";
  const scopePanel = document.getElementById("scope-panel");
  scopePanel.hidden = !run.scope_text;
  document.getElementById("scope-text").textContent = run.scope_text || "";
  const g1 = document.getElementById("gate1-actions");
  g1.innerHTML = "";
  if (run.phase === "awaiting_gate_1") {
    g1.append(
      button("Approve Gate 1", () => decide("approve", 1)),
      button("Revise", () => decide("revise", 1), "revise"),
      button("Discard", () => decide("discard", 1), "discard"),
    );
  }
  const brdPanel = document.getElementById("brd-panel");
  brdPanel.hidden = !run.brd_text && run.phase !== "awaiting_gate_2";
  document.getElementById("brd-text").value = run.brd_text || "";
  const g2 = document.getElementById("gate2-actions");
  g2.innerHTML = "";
  if (run.phase === "awaiting_gate_2") {
    g2.append(
      button("Approve Gate 2", () => decide("approve", 2)),
      button("Revise", () => decide("revise", 2), "revise"),
      button("Discard", () => decide("discard", 2), "discard"),
    );
  }
  document.getElementById("attestation-list").innerHTML = (run.attestations || [])
    .map((item) => {
      const predicate = item.predicate || {};
      const subject = (item.subject || [])[0] || {};
      return `<li>Gate ${escapeHtml(predicate.gate)} · ${escapeHtml(
        predicate.decision,
      )} · ${escapeHtml(predicate.actor)} · ${escapeHtml(subject.digest?.gitCommit || "")}</li>`;
    })
    .join("");
  const later = document.getElementById("later-panel");
  const laterActions = document.getElementById("later-actions");
  const gate = Number(run.awaiting);
  later.hidden = ![3, 4, 5, 6, 7].includes(gate) && run.phase !== "complete";
  laterActions.innerHTML = "";
  const laterBits = [
    run.architecture_text && "architecture locked",
    run.plan_text && "plan written",
    (run.build && run.build.engine) && `build engine ${run.build.engine}`,
    run.uat && run.uat.url && `uat ${run.uat.url}`,
    run.release && run.release.canary && `flag ${run.release.canary.flag}`,
  ].filter(Boolean);
  document.getElementById("later-text").textContent = laterBits.join("\n") || run.phase;
  document.getElementById("later-title").textContent =
    run.phase === "complete" ? "Complete" : `Gate ${run.awaiting || "—"}`;
  if (gate >= 3 && gate <= 7) {
    laterActions.append(button(`Approve Gate ${gate}`, () => decide("approve", gate)));
    if (gate <= 5) laterActions.append(button("Revise", () => decide("revise", gate), "revise"));
    if (gate === 6) laterActions.append(button("Reject UAT", () => decide("reject", 6), "discard"));
    if (gate === 7) laterActions.append(button("Hold", () => decide("hold", 7), "discard"));
  }
  const discardPanel = document.getElementById("discard-panel");
  discardPanel.hidden = !run.discarded_reason;
  document.getElementById("discard-reason").textContent = run.discarded_reason || "";
  if (connect) connectEvents(run.id);
}

function button(label, onClick, cls) {
  const b = document.createElement("button");
  b.type = "button";
  b.textContent = label;
  if (cls) b.className = cls;
  b.addEventListener("click", onClick);
  return b;
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

async function loadInbox() {
  const data = await api(`/api/inbox?id=${encodeURIComponent(actorSelect.value)}`);
  const ul = document.getElementById("inbox-list");
  ul.innerHTML = "";
  (data.inbox || []).forEach((row) => {
    const li = document.createElement("li");
    li.textContent = `${row.id} · gate ${row.awaiting} · ${row.phase}`;
    li.addEventListener("click", async () => {
      const run = await api(`/api/runs/${row.id}`);
      view("run");
      renderRun(run);
    });
    ul.appendChild(li);
  });
  if (!data.inbox?.length) {
    const li = document.createElement("li");
    li.textContent = "Nothing waiting for this identity.";
    ul.appendChild(li);
  }
}

async function loadRuns() {
  const data = await api("/api/runs");
  const list = document.getElementById("run-list");
  list.innerHTML = "";
  (data.runs || []).forEach((run) => {
    const li = document.createElement("li");
    li.textContent = `${run.id} · ${run.phase} · ${run.state}`;
    li.addEventListener("click", () => {
      view("run");
      renderRun(run);
    });
    list.appendChild(li);
  });
  if (!data.runs?.length) list.innerHTML = "<li>No runs yet.</li>";
}

async function connectEvents(id) {
  if (eventController) eventController.abort();
  eventController = new AbortController();
  const controller = eventController;
  const token = localStorage.getItem("phase1Token");
  const headers = token ? { Authorization: `Bearer ${token}` } : {};
  const status = document.getElementById("live-status");
  status.textContent = "Connecting";
  try {
    const response = await fetch(`/api/runs/${id}/events`, {
      headers,
      signal: controller.signal,
    });
    if (!response.ok || !response.body) throw new Error("live status unavailable");
    status.textContent = "Live";
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const frames = buffer.split("\n\n");
      buffer = frames.pop() || "";
      frames.forEach((frame) => {
        const data = frame
          .split("\n")
          .find((line) => line.startsWith("data: "));
        if (data && currentId === id) renderRun(JSON.parse(data.slice(6)), false);
      });
    }
  } catch (err) {
    if (err.name !== "AbortError") status.textContent = "Offline";
  }
}

boot();
