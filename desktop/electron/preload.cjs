const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("desktopApi", {
  openDataDirectory: () => ipcRenderer.invoke("app:open-data-directory"),
  openLogDirectory: () => ipcRenderer.invoke("app:open-log-directory")
});
