const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("nika", {
  backendUrl: "http://127.0.0.1:8756",
  onRefresh: (cb) => ipcRenderer.on("refresh", cb),
  onReminder: (cb) => ipcRenderer.on("reminder", (_e, r) => cb(r)),
  onLog: (cb) => ipcRenderer.on("backend-log", (_e, entry) => cb(entry)),
  getLogBuffer: () => ipcRenderer.invoke("get-log-buffer"),
});
