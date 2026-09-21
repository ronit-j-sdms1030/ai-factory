const actorSelect = document.getElementById("actor");
const errorEl = document.getElementById("error");
let currentId = null;

function showError(msg) {
  errorEl.hidden = !msg;
  errorEl.textContent = msg || "";
}

async function api(path, opts) {
  const res = await fetch(path, opts);
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
    const run = await api(`/api/runs/${currentId}/decide`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        actor_id: actorSelect.value,
        outcome,
        gate,
        channel: "workspace",
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

function renderRun(run) {
  currentId = run.id;
  document.getElementById("run-title").textContent = run.id;
  document.getElementById("run-meta").textContent =
    `${run.phase} · template ${run.template} · awaiting gate ${run.awaiting ?? "—"}` +
    (run.escalated ? " · SLA ESCALATED" : "") +
    (run.sla_due ? ` · SLA ${run.sla_due}` : "");
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
    const req = row.requirement || {};
    const li = document.createElement("li");
    li.textContent = `${req.id} · gate ${row.awaiting} · ${row.phase}`;
    li.addEventListener("click", async () => {
      const run = await api(`/api/runs/${req.id}`);
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

boot();
