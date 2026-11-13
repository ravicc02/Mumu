const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("desktopApi", {
  openExternal: url => ipcRenderer.invoke("app:open-project-link",url),
  openDataDirectory: () => ipcRenderer.invoke("app:open-data-directory"),
  openLogDirectory: () => ipcRenderer.invoke("app:open-log-directory")
});
