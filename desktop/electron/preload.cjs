const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("desktopApi", {
  bootstrapState: () => ipcRenderer.invoke("app:bootstrap:get"),
  checkDependencies: () => ipcRenderer.invoke("app:bootstrap:check"),
  installDependencies: () => ipcRenderer.invoke("app:bootstrap:install"),
  openExternal: url => ipcRenderer.invoke("app:open-project-link",url),
  openDataDirectory: () => ipcRenderer.invoke("app:open-data-directory"),
  openLogDirectory: () => ipcRenderer.invoke("app:open-log-directory")
});
