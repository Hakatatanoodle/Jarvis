// Nika desktop shell. Single device, no auth (see UI_HANDOVER_PROMPT.md
// open decision #2 — resolved: Electron app on one machine, so auth is
// out of scope). This process's only jobs: spawn the local backend,
// open a window pointed at it, and shut the backend down on quit.
const { app, BrowserWindow, ipcMain, Notification } = require("electron");
const { spawn } = require("child_process");
const path = require("path");
const http = require("http");
const fs = require("fs");

// Load a plain KEY=VALUE `.env` file (see .env.example) so NIKA_REPO_PATH,
// NIKA_PIPER_VOICE, etc. only need to be set ONCE, in a file, rather than
// re-exported by hand in every terminal session before `npm start`.
// Deliberately no dependency on the `dotenv` package for something this
// small — a few lines here avoids one more thing `npm install` has to
// fetch. Values already present in the real environment (an actual
// `export`) still win, so this never surprises someone who prefers that.
function loadDotEnv(envPath) {
  if (!fs.existsSync(envPath)) return;
  for (const line of fs.readFileSync(envPath, "utf8").split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const eq = trimmed.indexOf("=");
    if (eq === -1) continue;
    const key = trimmed.slice(0, eq).trim();
    const value = trimmed.slice(eq + 1).trim();
    if (!(key in process.env)) process.env[key] = value;
  }
}
loadDotEnv(path.join(__dirname, "..", ".env"));

const BACKEND_PORT = 8756;
const BACKEND_URL = `http://127.0.0.1:${BACKEND_PORT}`;
// Path to the existing jarvis repo checkout. Set NIKA_REPO_PATH in
// electron-app/.env (copy .env.example) rather than exporting it by hand.
const REPO_PATH = process.env.NIKA_REPO_PATH || path.join(__dirname, "..", "..", "jarvis");

let backendProcess = null;
let mainWindow = null;

// Prefer the repo's own venv python if one exists, so this works
// whether or not the shell that ran `npm start` has it activated —
// avoids silently falling back to system python3 (which won't have
// fastapi/uvicorn installed) and failing with no obvious error.
const venvPython = path.join(REPO_PATH, ".venv", "bin", "python3");
const PYTHON = fs.existsSync(venvPython) ? venvPython : "python3";

// In-memory ring buffer so the Logs panel has something to show even
// if it's opened after the backend already printed a bunch of lines
// (e.g. app just started).
const LOG_BUFFER = [];
const LOG_BUFFER_MAX = 2000;

function pushLog(stream, text) {
  for (const line of text.split("\n")) {
    if (!line) continue;
    const entry = { stream, line, ts: Date.now() };
    LOG_BUFFER.push(entry);
    if (LOG_BUFFER.length > LOG_BUFFER_MAX) LOG_BUFFER.shift();
    if (mainWindow) mainWindow.webContents.send("backend-log", entry);
  }
  // Still print to the terminal too — nothing lost for anyone tailing it.
  (stream === "stderr" ? process.stderr : process.stdout).write(text);
}

ipcMain.handle("get-log-buffer", () => LOG_BUFFER);

function startBackend() {
  // Charger-noise workaround from mic debugging: PipeWire sometimes
  // resets capture level, so pin it every launch. Best-effort — if
  // amixer isn't present or the card index differs, this just no-ops
  // rather than blocking startup.
  try {
    spawn("amixer", ["-c", "0", "sset", "Capture", "50%"], { stdio: "ignore" });
  } catch {
    // non-fatal
  }
  backendProcess = spawn(
    PYTHON,
    ["-m", "uvicorn", "server.app:app", "--port", String(BACKEND_PORT)],
    { cwd: REPO_PATH, stdio: ["ignore", "pipe", "pipe"] }
  );
  backendProcess.stdout.on("data", (d) => pushLog("stdout", d.toString()));
  backendProcess.stderr.on("data", (d) => pushLog("stderr", d.toString()));
  backendProcess.on("exit", (code) => {
    if (code !== 0 && code !== null) {
      pushLog("stderr", `[backend exited with code ${code}]\n`);
    }
  });
}

function waitForBackend(retries = 40) {
  return new Promise((resolve, reject) => {
    const attempt = (n) => {
      http.get(`${BACKEND_URL}/status`, (res) => {
        res.resume();
        resolve();
      }).on("error", () => {
        if (n <= 0) return reject(new Error("backend did not start in time"));
        setTimeout(() => attempt(n - 1), 250);
      });
    };
    attempt(retries);
  });
}

// Reminders (Capabilities V2): poll-based delivery. GET /reminders/due
// returns each due reminder exactly once (the backend marks it delivered
// atomically), so overlapping polls are harmless. Runs on launch, on
// window focus, and every 30s while the app is running — even unfocused.
// KNOWN LIMITATION: nothing fires while the app is fully closed; a
// reminder due then is delivered on next launch.
const REMINDER_POLL_MS = 30000;

function pollReminders() {
  http.get(`${BACKEND_URL}/reminders/due`, (res) => {
    let body = "";
    res.on("data", (d) => (body += d));
    res.on("end", () => {
      let due = [];
      try { due = JSON.parse(body); } catch { return; }
      if (!Array.isArray(due)) return;
      for (const r of due) {
        if (Notification.isSupported()) new Notification({ title: "Nika reminder", body: r.text }).show();
        if (mainWindow) mainWindow.webContents.send("reminder", r);
      }
    });
  }).on("error", () => { /* backend not up yet — next tick */ });
}

async function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1100,
    height: 760,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      // Chromium normally blocks audio.play() unless it happens
      // synchronously inside a user gesture — but TTS playback fires
      // after an `await fetch()` for the audio blob, so by the time
      // it's ready the browser no longer counts it as gesture-driven
      // and silently drops it. This is our own app's content, not an
      // untrusted web page, so removing that restriction here is safe.
      autoplayPolicy: "no-user-gesture-required",
    },
  });
  // Renderer polls the backend itself via fetch(); the preload script
  // just exposes the backend URL and a "poll on focus" hook (open
  // decision #3: polling-on-focus, not push, per your go-ahead).
  await mainWindow.loadFile(path.join(__dirname, "renderer", "index.html"));
  mainWindow.on("focus", () => {
    mainWindow.webContents.send("refresh");
    pollReminders();
  });
  pollReminders();
  setInterval(pollReminders, REMINDER_POLL_MS);
}

app.whenReady().then(async () => {
  // Electron blocks getUserMedia by default until the app explicitly
  // grants it — without this, the renderer's mic button fails silently
  // with a permission-denied error that looks identical to "no mic."
  const { session } = require("electron");
  session.defaultSession.setPermissionRequestHandler((webContents, permission, callback) => {
    callback(permission === "media");
  });

  startBackend();
  try {
    await waitForBackend();
  } catch (e) {
    console.error(e);
  }
  await createWindow();
});

app.on("window-all-closed", () => {
  if (backendProcess) backendProcess.kill();
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => {
  if (backendProcess) backendProcess.kill();
});
