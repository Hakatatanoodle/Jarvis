const BASE = window.nika.backendUrl;
const viewEl = document.getElementById("view");
const statusEl = document.getElementById("statusbar");
let currentView = "chat";

// -- sidebar --
document.querySelectorAll("#sidebar button").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("#sidebar button").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    currentView = btn.dataset.view;
    render();
  });
});

// -- status bar (replaces CLI /status) --
async function refreshStatus() {
  try {
    const r = await fetch(`${BASE}/status`);
    const s = await r.json();
    statusEl.textContent = s.mission ? `Mission: ${s.mission.title}` : "No active mission — set one in the Mission panel";
  } catch {
    statusEl.textContent = "Backend unreachable";
  }
}

// -- chat view --
function chatView() {
  viewEl.innerHTML = `<div id="chatlog"></div>`;
}

function appendMsg(role, text) {
  const log = document.getElementById("chatlog");
  if (!log) return;
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.textContent = text;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}

function appendCard(card) {
  const log = document.getElementById("chatlog");
  if (!log) return;
  const div = document.createElement("div");
  div.className = "card";
  div.innerHTML = `
    <div><strong>${card.title}</strong></div>
    ${card.detail ? `<div>${card.detail}</div>` : ""}
    <div class="reason">${card.reason}</div>
    <button class="yes">Approve</button>
    <button class="no">Reject</button>
  `;
  div.querySelector(".yes").addEventListener("click", () => resolveCard(card.id, true, div));
  div.querySelector(".no").addEventListener("click", () => resolveCard(card.id, false, div));
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}

async function resolveCard(id, approved, div) {
  div.querySelectorAll("button").forEach((b) => (b.disabled = true));
  const r = await fetch(`${BASE}/pending/resolve`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id, approved }),
  });
  const { note } = await r.json();
  div.innerHTML += `<div class="reason">${approved ? "Approved" : "Rejected"}${note ? " — " + note : ""}</div>`;
}

async function sendMessage() {
  const input = document.getElementById("chatinput");
  const sendBtn = document.getElementById("sendbtn");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  appendMsg("user", text);

  input.disabled = true;
  sendBtn.disabled = true;
  const typingEl = appendTyping();

  try {
    const r = await fetch(`${BASE}/chat`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (!r.ok) throw new Error(`backend returned ${r.status}`);
    const data = await r.json();
    typingEl.remove();
    appendMsg("nika", data.response_text);
    data.pending.forEach(appendCard);
  } catch (err) {
    typingEl.remove();
    appendMsg("nika", `⚠ Couldn't reach Nika (${err.message}). Check the Logs tab.`);
  } finally {
    input.disabled = false;
    sendBtn.disabled = false;
    input.focus();
  }
}

function appendTyping() {
  const log = document.getElementById("chatlog");
  const div = document.createElement("div");
  div.className = "typing";
  div.innerHTML = `Nika is thinking<span class="dot">.</span><span class="dot">.</span><span class="dot">.</span>`;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
  return div;
}

document.getElementById("sendbtn").addEventListener("click", sendMessage);
document.getElementById("chatinput").addEventListener("keydown", (e) => {
  if (e.key === "Enter") sendMessage();
});

// -- goals panel --
async function goalsView() {
  const goals = await (await fetch(`${BASE}/goals`)).json();
  viewEl.innerHTML = `<h2>Goals</h2><table>
    <tr><th>Title</th><th>Type</th><th>Status</th><th>Priority</th></tr>
    ${goals.map((g) => `<tr><td>${g.title}</td><td>${g.type}</td><td>${g.status}</td><td>${g.priority}</td></tr>`).join("")}
  </table>`;
}

// -- mission panel --
async function missionView() {
  const m = await (await fetch(`${BASE}/mission`)).json();
  viewEl.innerHTML = m
    ? `<h2>Mission</h2><p><strong>${m.title}</strong></p><p>${m.statement}</p>
       <p>${(m.principles || []).map((p) => `• ${p}`).join("<br/>")}</p>`
    : `<h2>Mission</h2><p>No active mission.</p>`;
}

// -- memory panel --
async function memoryView() {
  const mems = await (await fetch(`${BASE}/memories?status=Active`)).json();
  viewEl.innerHTML = `<h2>Memory</h2><table>
    <tr><th>Title</th><th>Type</th><th>Value</th></tr>
    ${mems.map((m) => `<tr><td>${m.title}</td><td>${m.type}</td><td>${m.value}</td></tr>`).join("")}
  </table>`;
}

// -- decisions panel --
async function decisionsView() {
  const decisions = await (await fetch(`${BASE}/decisions`)).json();
  viewEl.innerHTML = `<h2>Decisions</h2><table>
    <tr><th>Intent</th><th>Objective</th><th>Created</th></tr>
    ${decisions.map((d) => `<tr><td>${d.intent}</td><td>${d.objective || ""}</td><td>${d.created_at || ""}</td></tr>`).join("")}
  </table>`;
}

// -- logs panel --
async function logsView() {
  viewEl.innerHTML = `<h2>Backend logs</h2><div id="logview"></div>`;
  const buf = await window.nika.getLogBuffer();
  const el = document.getElementById("logview");
  el.innerHTML = buf.map(logLineHtml).join("\n");
  el.scrollTop = el.scrollHeight;
}

function logLineHtml(entry) {
  const cls = entry.stream === "stderr" ? "stderr" : "stdout";
  return `<span class="${cls}">${escapeHtml(entry.line)}</span>`;
}

function escapeHtml(s) {
  return s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

window.nika.onLog((entry) => {
  if (currentView !== "logs") return;
  const el = document.getElementById("logview");
  if (!el) return;
  el.innerHTML += "\n" + logLineHtml(entry);
  el.scrollTop = el.scrollHeight;
});

function render() {
  if (currentView === "chat") chatView();
  else if (currentView === "goals") goalsView();
  else if (currentView === "mission") missionView();
  else if (currentView === "memory") memoryView();
  else if (currentView === "decisions") decisionsView();
  else if (currentView === "logs") logsView();
}

window.nika.onRefresh(() => {
  refreshStatus();
  if (currentView !== "chat") render(); // chat is append-only, don't reset it
});

refreshStatus();
render();
