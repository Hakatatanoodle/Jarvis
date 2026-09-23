const BASE = window.nika.backendUrl;
const viewEl = document.getElementById("view");
const statusEl = document.getElementById("statusbar");
let currentView = "chat";

// Created once and re-attached on every visit to the Chat tab, instead
// of being rebuilt from viewEl.innerHTML each time — that used to wipe
// the visible conversation on every tab switch even though the backend
// itself remembers everything (conversation_turn table). This element
// is now the single source of truth for what's on screen; append*
// functions below target it directly rather than re-querying the DOM,
// so a message can still land correctly even if the user isn't
// currently looking at the Chat tab.
const chatLogEl = document.createElement("div");
chatLogEl.id = "chatlog";

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

// -- small DOM helpers (textContent-based — see fix for architecture
// review point 3: goals/memory/mission/decisions/card data ultimately
// comes from the database, so it's untrusted as far as the DOM is
// concerned; building elements this way means a goal titled
// "<img onerror=...>" just displays as that literal text, it can't
// execute) --
function el(tag, props = {}, children = []) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "className") e.className = v;
    else if (k === "text") e.textContent = v;
    else e.setAttribute(k, v);
  }
  for (const c of children) e.appendChild(c);
  return e;
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

// -- chat view --
function chatView() {
  clear(viewEl);
  viewEl.appendChild(chatLogEl);
}

function appendMsg(role, text) {
  const div = el("div", { className: `msg ${role}`, text });
  chatLogEl.appendChild(div);
  chatLogEl.scrollTop = chatLogEl.scrollHeight;
}

function appendCard(card) {
  const div = el("div", { className: "card" });
  div.appendChild(el("div", {}, [el("strong", { text: card.title })]));
  if (card.detail) div.appendChild(el("div", { text: card.detail }));
  div.appendChild(el("div", { className: "reason", text: card.reason }));
  const yesBtn = el("button", { className: "yes", text: "Approve" });
  const noBtn = el("button", { className: "no", text: "Reject" });
  yesBtn.addEventListener("click", () => resolveCard(card.id, true, div));
  noBtn.addEventListener("click", () => resolveCard(card.id, false, div));
  div.appendChild(yesBtn);
  div.appendChild(noBtn);
  chatLogEl.appendChild(div);
  chatLogEl.scrollTop = chatLogEl.scrollHeight;
}

async function resolveCard(id, approved, div) {
  div.querySelectorAll("button").forEach((b) => (b.disabled = true));
  const r = await fetch(`${BASE}/pending/resolve`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id, approved }),
  });
  const { note } = await r.json();
  const resultText = `${approved ? "Approved" : "Rejected"}${note ? " — " + note : ""}`;
  div.appendChild(el("div", { className: "reason", text: resultText }));
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
    speakText(data.response_text);
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
  const div = el("div", { className: "typing" });
  div.appendChild(document.createTextNode("Nika is thinking"));
  for (let i = 0; i < 3; i++) div.appendChild(el("span", { className: "dot", text: "." }));
  chatLogEl.appendChild(div);
  chatLogEl.scrollTop = chatLogEl.scrollHeight;
  return div;
}

// -- voice output (TTS, opt-in via speaker toggle) --
// Persisted across restarts with localStorage — safe here since this
// is a real desktop app's own renderer process, not a sandboxed
// artifact preview. Off by default the very first run (a silent app
// is a safer default than one that starts talking unprompted); once
// toggled, the choice sticks.
let voiceEnabled = localStorage.getItem("nika-voice-enabled") === "true";
const speakerBtn = document.getElementById("speakerbtn");
function updateSpeakerBtn() {
  speakerBtn.classList.toggle("muted", !voiceEnabled);
}
updateSpeakerBtn();
speakerBtn.addEventListener("click", () => {
  voiceEnabled = !voiceEnabled;
  localStorage.setItem("nika-voice-enabled", String(voiceEnabled));
  updateSpeakerBtn();
});

let currentAudio = null;
let audioCtx = null;
let activeSources = [];

function stopSpeaking() {
  // Barge-in: a new reply starting to speak should cut off whatever's
  // still playing from the previous one, rather than overlapping.
  activeSources.forEach((s) => { try { s.stop(); } catch { /* already finished */ } });
  activeSources = [];
}

async function speakText(text) {
  if (!voiceEnabled || !text) return;
  stopSpeaking();
  try {
    const r = await fetch(`${BASE}/voice/speak`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (!r.ok) throw new Error(`backend returned ${r.status}`);

    const sampleRate = parseInt(r.headers.get("X-Sample-Rate") || "22050", 10);
    if (!audioCtx || audioCtx.sampleRate !== sampleRate) {
      if (audioCtx) audioCtx.close();
      audioCtx = new AudioContext({ sampleRate });
    }
    if (audioCtx.state === "suspended") await audioCtx.resume();

    // Response body is a raw 16-bit PCM stream (see server/app.py's
    // /voice/speak) — no WAV wrapper, since a WAV header needs the
    // total length known up front, which defeats the point of
    // streaming. Each chunk is decoded and scheduled to start exactly
    // where the previous one ends, so playback is gapless even though
    // it's arriving incrementally over the network.
    let nextStartTime = audioCtx.currentTime;
    let leftover = new Uint8Array(0);
    const reader = r.body.getReader();
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      const combined = new Uint8Array(leftover.length + value.length);
      combined.set(leftover);
      combined.set(value, leftover.length);
      const usableLen = combined.length - (combined.length % 2); // int16 = 2 bytes
      leftover = combined.slice(usableLen);
      if (usableLen === 0) continue;

      const int16 = new Int16Array(combined.buffer, 0, usableLen / 2);
      const float32 = new Float32Array(int16.length);
      for (let i = 0; i < int16.length; i++) float32[i] = int16[i] / 32768;

      const audioBuffer = audioCtx.createBuffer(1, float32.length, sampleRate);
      audioBuffer.copyToChannel(float32, 0);
      const source = audioCtx.createBufferSource();
      source.buffer = audioBuffer;
      source.connect(audioCtx.destination);
      const startAt = Math.max(nextStartTime, audioCtx.currentTime);
      source.start(startAt);
      nextStartTime = startAt + audioBuffer.duration;
      activeSources.push(source);
    }
  } catch (err) {
    // Non-fatal — TTS failing (e.g. NIKA_PIPER_VOICE not set, or
    // autoplay blocked) shouldn't break the chat itself, but a purely
    // silent failure meant this was hard to debug from the app alone
    // — so at least show a one-line note instead of only logging to
    // devtools console.
    console.error("TTS failed:", err);
    appendMsg("nika", `⚠ (voice playback failed: ${err.message})`);
  }
}

document.getElementById("sendbtn").addEventListener("click", sendMessage);
document.getElementById("chatinput").addEventListener("keydown", (e) => {
  if (e.key === "Enter") sendMessage();
});

// -- voice input (push-to-talk, transcript-only, no auto-send) --
// Click once to start recording, click again to stop. On stop, the
// clip is sent to /voice/transcribe (local faster-whisper — see
// server/app.py) and the resulting text is dropped into the input box
// for review, exactly as if typed. Nothing is sent to Nika until the
// person hits Send themselves.
let mediaRecorder = null;
let recordedChunks = [];

async function toggleRecording() {
  const micBtn = document.getElementById("micbtn");
  if (mediaRecorder && mediaRecorder.state === "recording") {
    mediaRecorder.stop();
    return;
  }
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    appendMsg("nika", `⚠ Couldn't access the microphone (${err.message}). Check system input settings.`);
    return;
  }
  recordedChunks = [];
  mediaRecorder = new MediaRecorder(stream);
  mediaRecorder.ondataavailable = (e) => { if (e.data.size > 0) recordedChunks.push(e.data); };
  mediaRecorder.onstop = async () => {
    stream.getTracks().forEach((t) => t.stop());
    micBtn.classList.remove("recording");
    micBtn.textContent = "🎤";
    await sendForTranscription(new Blob(recordedChunks, { type: "audio/webm" }));
  };
  mediaRecorder.start();
  micBtn.classList.add("recording");
  micBtn.textContent = "⏹";
}

async function sendForTranscription(blob) {
  const input = document.getElementById("chatinput");
  const prevPlaceholder = input.placeholder;
  input.placeholder = "Transcribing…";
  input.disabled = true;
  try {
    const form = new FormData();
    form.append("audio", blob, "clip.webm");
    const r = await fetch(`${BASE}/voice/transcribe`, { method: "POST", body: form });
    if (!r.ok) throw new Error(`backend returned ${r.status}`);
    const { text } = await r.json();
    input.value = text || "";
  } catch (err) {
    appendMsg("nika", `⚠ Transcription failed (${err.message}). Check the Logs tab.`);
  } finally {
    input.disabled = false;
    input.placeholder = prevPlaceholder;
    input.focus();
  }
}

document.getElementById("micbtn").addEventListener("click", toggleRecording);

// -- goals panel --
function goalsRow(g) {
  return el("tr", {}, [
    el("td", { text: g.title }), el("td", { text: g.type }),
    el("td", { text: g.status }), el("td", { text: String(g.priority) }),
  ]);
}

async function goalsView() {
  const goals = await (await fetch(`${BASE}/goals`)).json();
  clear(viewEl);
  const table = el("table", {}, [
    el("tr", {}, ["Title", "Type", "Status", "Priority"].map((h) => el("th", { text: h }))),
    ...goals.map(goalsRow),
  ]);
  viewEl.appendChild(el("h2", { text: "Goals" }));
  viewEl.appendChild(table);
}

// -- mission panel --
async function missionView() {
  const m = await (await fetch(`${BASE}/mission`)).json();
  clear(viewEl);
  viewEl.appendChild(el("h2", { text: "Mission" }));
  if (!m) {
    viewEl.appendChild(el("p", { text: "No active mission." }));
    return;
  }
  viewEl.appendChild(el("p", {}, [el("strong", { text: m.title })]));
  viewEl.appendChild(el("p", { text: m.statement }));
  const principles = el("p");
  (m.principles || []).forEach((p, i) => {
    if (i > 0) principles.appendChild(el("br"));
    principles.appendChild(document.createTextNode(`• ${p}`));
  });
  viewEl.appendChild(principles);
}

// -- memory panel --
function memoryRow(m) {
  return el("tr", {}, [
    el("td", { text: m.title }), el("td", { text: m.type }), el("td", { text: m.value }),
  ]);
}

async function memoryView() {
  const mems = await (await fetch(`${BASE}/memories?status=Active`)).json();
  clear(viewEl);
  const table = el("table", {}, [
    el("tr", {}, ["Title", "Type", "Value"].map((h) => el("th", { text: h }))),
    ...mems.map(memoryRow),
  ]);
  viewEl.appendChild(el("h2", { text: "Memory" }));
  viewEl.appendChild(table);
}

// -- decisions panel --
function decisionRow(d) {
  return el("tr", {}, [
    el("td", { text: d.intent }), el("td", { text: d.objective || "" }), el("td", { text: d.created_at || "" }),
  ]);
}

async function decisionsView() {
  const decisions = await (await fetch(`${BASE}/decisions`)).json();
  clear(viewEl);
  const table = el("table", {}, [
    el("tr", {}, ["Intent", "Objective", "Created"].map((h) => el("th", { text: h }))),
    ...decisions.map(decisionRow),
  ]);
  viewEl.appendChild(el("h2", { text: "Decisions" }));
  viewEl.appendChild(table);
}

// -- logs panel --
function logLineEl(entry) {
  return el("span", { className: entry.stream === "stderr" ? "stderr" : "stdout", text: entry.line + "\n" });
}

async function logsView() {
  clear(viewEl);
  viewEl.appendChild(el("h2", { text: "Backend logs" }));
  const logDiv = el("div", { id: "logview" });
  const buf = await window.nika.getLogBuffer();
  buf.forEach((entry) => logDiv.appendChild(logLineEl(entry)));
  viewEl.appendChild(logDiv);
  logDiv.scrollTop = logDiv.scrollHeight;
}

window.nika.onLog((entry) => {
  if (currentView !== "logs") return;
  const logDiv = document.getElementById("logview");
  if (!logDiv) return;
  logDiv.appendChild(logLineEl(entry));
  logDiv.scrollTop = logDiv.scrollHeight;
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

// Reminders (Capabilities V2): main.js polls /reminders/due and forwards
// each due reminder here (it also shows the OS notification itself).
window.nika.onReminder((r) => appendMsg("nika", `⏰ Reminder: ${r.text}`));

refreshStatus();
render();
