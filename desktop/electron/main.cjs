const { app, BrowserWindow, ipcMain, Menu, nativeImage, shell, Tray } = require("electron");
const { spawn } = require("node:child_process");
const path = require("node:path");
const http = require("node:http");
const product = require("../product.json");
const { resolveDataRoot } = require("./data-root.cjs");
const { allowedProjectLink } = require("./external-links.cjs");
const { createBootstrap, verifyBackendHealth } = require("./bootstrap.cjs");
const { readWindowsLocation } = require("./windows-location.cjs");
const { backendExecutable, createLinuxAutoLaunch } = require("./platform.cjs");

let backendProcess = null;
let mainWindow = null;
let bootstrap = null;
let tray = null;
let quitting = false;
const isDev = !app.isPackaged;
app.setName(product.name);
if (process.platform === "win32") app.setAppUserModelId(product.appId);
const singleInstance = app.requestSingleInstanceLock();
if (!singleInstance) app.quit();

function dataRoot() {
  return resolveDataRoot({ home:app.getPath("home"), product });
}

function configuredPythonCommand() {
  return process.env.MUMU_PYTHON || process.env.ANSWER_STUDIO_PYTHON || (process.platform === "win32" ? "python" : "python3");
}

function backendDirectory() {
  return isDev
    ? path.resolve(__dirname, "..", "..", "backend")
    : path.join(process.resourcesPath, "backend");
}

function waitForBackend(timeoutMs = 60000) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    let done = false;
    const finish = error => { if (done) return; done = true; if (error) reject(error); else resolve(); };
    const poll = () => {
      if (done) return;
      let handled = false;
      const next = () => { if (!handled) { handled = true; retry(); } };
      const request = http.get("http://127.0.0.1:8877/api/health", (response) => {
        if (response.statusCode !== 200) { response.resume(); next(); return; }
        let body = ''; response.setEncoding('utf8');
        response.on('data', chunk => { body += chunk; if (body.length > 16384) response.destroy(new Error('Invalid health response')); });
        response.on('error', next);
        response.on('end', () => {
          try { verifyBackendHealth(JSON.parse(body), require('../package.json').version); handled = true; finish(); }
          catch (error) { if (error.code === 'VERSION_MISMATCH') finish(error); else next(); }
        });
      });
      request.on("error", next);
      request.setTimeout(1200, () => {
        request.destroy();
        next();
      });
    };
    const retry = () => {
      if (done) return;
      if (Date.now() >= deadline) {
        finish(new Error("本地后端启动超时，请检查日志或使用手动启动命令。"));
        return;
      }
      setTimeout(poll, 500);
    };
    poll();
  });
}

function startBackend(pythonCommand) {
  if (backendProcess) {
    return;
  }
  const command = isDev
    ? (pythonCommand || configuredPythonCommand())
    : path.join(backendDirectory(), backendExecutable(product));
  const args = isDev
    ? [
        "run_server.py",
        "--host",
        "127.0.0.1",
        "--port",
        "8877"
      ]
    : ["--host", "127.0.0.1", "--port", "8877"];
  backendProcess = spawn(command, args, {
    cwd: backendDirectory(),
    windowsHide: true,
    env: {
      ...process.env,
      PYTHONUNBUFFERED: "1"
    }
  });
  backendProcess.stdout.on("data", (data) => {
    console.log(`[backend] ${data.toString().trim()}`);
  });
  backendProcess.stderr.on("data", (data) => {
    console.error(`[backend] ${data.toString().trim()}`);
  });
  backendProcess.on("exit", (code) => {
    console.log(`Backend exited with code ${code}`);
    backendProcess = null;
  });
  backendProcess.on("error", error => {
    console.error("Backend failed to start:", error.message);
    backendProcess = null;
  });
}

function showMainWindow() {
  if (!app.isReady()) {
    void app.whenReady().then(showMainWindow);
    return;
  }
  if (!mainWindow || mainWindow.isDestroyed()) {
    void createWindow();
    return;
  }
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show();
  mainWindow.focus();
}

function createTray() {
  if (tray) return;
  const icon = nativeImage.createFromPath(path.join(__dirname, "..", "build", "icon.png"));
  try { tray = new Tray(icon.resize({ width: process.platform === "linux" ? 32 : 16, height: process.platform === "linux" ? 32 : 16 })); }
  catch (error) { console.warn("Mumu tray unavailable:", error.message); return; }
  tray.setToolTip(product.name);
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: "打开 Mumu", click: showMainWindow },
    { type: "separator" },
    { label: "退出 Mumu", click: () => { quitting = true; app.quit(); } }
  ]));
  tray.on("click", showMainWindow);
  tray.on("double-click", showMainWindow);
}

async function createWindow() {
  if (mainWindow && !mainWindow.isDestroyed()) {
    showMainWindow();
    return;
  }
  mainWindow = new BrowserWindow({
    width: 1480,
    height: 960,
    minWidth: 1180,
    minHeight: 760,
    backgroundColor: "#f3f5f2",
    title: product.name,
    icon: path.join(__dirname, "..", "build", "icon.png"),
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false
    }
  });
  mainWindow.on("close", event => {
    if (quitting) return;
    event.preventDefault();
    if (process.platform === "linux" || !tray) mainWindow.minimize();
    else mainWindow.hide();
  });
  mainWindow.on("closed", () => {
    mainWindow = null;
  });
  mainWindow.setMenuBarVisibility(false);
  mainWindow.webContents.session.setPermissionCheckHandler((webContents, permission) => (
    permission === "geolocation" && webContents === mainWindow.webContents
  ));
  mainWindow.webContents.session.setPermissionRequestHandler((webContents, permission, callback) => {
    callback(permission === "geolocation" && webContents === mainWindow.webContents);
  });
  bootstrap = createBootstrap({
    packaged: app.isPackaged, backendDirectory: backendDirectory(),
    dataRoot: dataRoot(),
    python: configuredPythonCommand(),
    onReady: async python => {
      try { await waitForBackend(1500); return; } catch (error) { if (error.code === 'VERSION_MISMATCH') throw error; }
      startBackend(python);
      await waitForBackend();
    }
  });
  void bootstrap.check();

  if (isDev) {
    await mainWindow.loadURL("http://127.0.0.1:5174");
  } else {
    await mainWindow.loadFile(path.join(__dirname, "..", "dist", "index.html"));
  }
}

app.on("second-instance", showMainWindow);

app.whenReady().then(async () => {
  if (!singleInstance) return;
  Menu.setApplicationMenu(null);
  createTray();
  await createWindow();
});

app.on("window-all-closed", () => {
  if (!tray && process.platform !== "darwin") {
    app.quit();
  }
});

app.on("activate", () => {
  showMainWindow();
});

app.on("before-quit", () => {
  quitting = true;
  if (tray) {
    tray.destroy();
    tray = null;
  }
  if (backendProcess) {
    backendProcess.kill();
    backendProcess = null;
  }
});

ipcMain.handle("app:open-project-link", async (_event,url) => {
  if(!allowedProjectLink(url)) throw new Error("项目链接无效");
  await shell.openExternal(url);
  return true;
});

for (const action of ["get", "check", "install"]) {
  ipcMain.handle("app:bootstrap:" + action, event => {
    if (!mainWindow || event.sender !== mainWindow.webContents || event.senderFrame !== mainWindow.webContents.mainFrame) throw new Error("启动操作来源无效");
    if (!bootstrap) throw new Error("启动环境尚未就绪");
    return bootstrap[action]();
  });
}

function loginItemOptions() {
  return { path:process.execPath,args:[] };
}

function autoLaunchSupported() {
  return ["win32", "linux"].includes(process.platform) && app.isPackaged;
}

ipcMain.handle("app:get-auto-launch", event => {
  if (!mainWindow || event.sender !== mainWindow.webContents || event.senderFrame !== mainWindow.webContents.mainFrame) {
    throw new Error("开机自启设置来源无效");
  }
  if (!autoLaunchSupported()) return { supported:false,enabled:false };
  if (process.platform === "linux") return createLinuxAutoLaunch().get();
  const settings = app.getLoginItemSettings(loginItemOptions());
  return { supported:true,enabled:Boolean(settings.openAtLogin) };
});

ipcMain.handle("app:set-auto-launch", (event,enabled) => {
  if (!mainWindow || event.sender !== mainWindow.webContents || event.senderFrame !== mainWindow.webContents.mainFrame) {
    throw new Error("开机自启设置来源无效");
  }
  if (typeof enabled !== "boolean") throw new Error("开机自启设置值无效");
  if (!autoLaunchSupported()) throw new Error("开机自启仅支持安装后的桌面版本");
  if (process.platform === "linux") return createLinuxAutoLaunch().set(enabled);
  app.setLoginItemSettings({ ...loginItemOptions(),openAtLogin:enabled });
  const settings = app.getLoginItemSettings(loginItemOptions());
  return { supported:true,enabled:Boolean(settings.openAtLogin) };
});

ipcMain.handle("app:open-data-directory", async () => {
  const target = dataRoot();
  const error = await shell.openPath(target);
  if(error) throw new Error(error);
  return target;
});

ipcMain.handle("app:open-log-directory", async () => {
  const logsRoot = path.join(dataRoot(), "logs");
  const error = await shell.openPath(logsRoot);
  if(error) throw new Error(error);
  return logsRoot;
});

ipcMain.handle("app:open-location-settings", async (event) => {
  if (!mainWindow || event.sender !== mainWindow.webContents || event.senderFrame !== mainWindow.webContents.mainFrame) {
    throw new Error("定位设置来源无效");
  }
  if (process.platform !== "win32") throw new Error("当前系统不支持 Windows 定位设置");
  await shell.openExternal("ms-settings:privacy-location");
  return true;
});

ipcMain.handle("app:get-windows-location", async (event) => {
  if (!mainWindow || event.sender !== mainWindow.webContents || event.senderFrame !== mainWindow.webContents.mainFrame) {
    throw new Error("定位请求来源无效");
  }
  if (process.platform !== "win32") throw new Error("当前系统不支持 Windows 定位");
  try {
    return await readWindowsLocation();
  } catch {
    throw new Error("无法读取 Windows 当前位置，请检查系统定位和隐私权限");
  }
});
