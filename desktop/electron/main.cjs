const { app, BrowserWindow, ipcMain, Menu, shell } = require("electron");
const { spawn } = require("node:child_process");
const path = require("node:path");
const http = require("node:http");
const product = require("../product.json");
const { allowedProjectLink } = require("./external-links.cjs");
const { createBootstrap, verifyBackendHealth } = require("./bootstrap.cjs");

let backendProcess = null;
let mainWindow = null;
let bootstrap = null;
const isDev = !app.isPackaged;
app.setName(product.name);
if (process.platform === "win32") app.setAppUserModelId(product.appId);

function dataRoot() {
  return process.env.MUMU_DATA
    || process.env.ANSWER_STUDIO_DATA
    || path.join(process.env.LOCALAPPDATA || app.getPath("userData"), product.dataDirectory);
}

function configuredPythonCommand() {
  return process.env.MUMU_PYTHON || process.env.ANSWER_STUDIO_PYTHON || "python";
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
    : path.join(backendDirectory(), product.backendExecutable);
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

async function createWindow() {
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

app.whenReady().then(async () => {
  Menu.setApplicationMenu(null);
  await createWindow();
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow();
  }
});

app.on("before-quit", () => {
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
