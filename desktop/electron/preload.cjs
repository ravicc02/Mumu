const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("desktopApi", {
  platform: process.platform,
  bootstrapState: () => ipcRenderer.invoke("app:bootstrap:get"),
  checkDependencies: () => ipcRenderer.invoke("app:bootstrap:check"),
  installDependencies: () => ipcRenderer.invoke("app:bootstrap:install"),
  getAutoLaunch: () => ipcRenderer.invoke("app:get-auto-launch"),
  setAutoLaunch: enabled => ipcRenderer.invoke("app:set-auto-launch",Boolean(enabled)),
  openExternal: url => ipcRenderer.invoke("app:open-project-link",url),
  openDataDirectory: () => ipcRenderer.invoke("app:open-data-directory"),
  openLogDirectory: () => ipcRenderer.invoke("app:open-log-directory"),
  ...(process.platform === "win32" ? {
    openLocationSettings: () => ipcRenderer.invoke("app:open-location-settings"),
    getWindowsLocation: () => ipcRenderer.invoke("app:get-windows-location")
  } : {})
});
