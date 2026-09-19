// Nika desktop shell. Single device, no auth (see UI_HANDOVER_PROMPT.md
// open decision #2 — resolved: Electron app on one machine, so auth is
// out of scope). This process's only jobs: spawn the local backend,
// open a window pointed at it, and shut the backend down on quit.
const { app, BrowserWindow, ipcMain } = require("electron");
const { spawn } = require("child_process");
const path = require("path");
const http = require("http");

const BACKEND_PORT = 8756;
const BACKEND_URL = `http://127.0.0.1:${BACKEND_PORT}`;
// Path to the existing jarvis repo checkout. Adjust for your machine,
// or set NIKA_REPO_PATH before launching.
const REPO_PATH = process.env.NIKA_REPO_PATH || path.join(__dirname, "..", "..", "jarvis");

let backendProcess = null;
let mainWindow = null;

// Prefer the repo's own venv python if one exists, so this works
// whether or not the shell that ran `npm start` has it activated —
// avoids silently falling back to system python3 (which won't have
// fastapi/uvicorn installed) and failing with no obvious error.
const fs = require("fs");
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

async function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1100,
    height: 760,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
    },
  });
  // Renderer polls the backend itself via fetch(); the preload script
  // just exposes the backend URL and a "poll on focus" hook (open
  // decision #3: polling-on-focus, not push, per your go-ahead).
  await mainWindow.loadFile(path.join(__dirname, "renderer", "index.html"));
  mainWindow.on("focus", () => mainWindow.webContents.send("refresh"));
}

app.whenReady().then(async () => {
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
