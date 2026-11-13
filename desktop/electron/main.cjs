const { app, BrowserWindow, ipcMain, shell } = require("electron");
const { spawn } = require("node:child_process");
const path = require("node:path");
const http = require("node:http");
const { allowedProjectLink } = require("./external-links.cjs");

let backendProcess = null;
let mainWindow = null;
const isDev = !app.isPackaged;

function backendDirectory() {
  return isDev
    ? path.resolve(__dirname, "..", "..", "backend")
    : path.join(process.resourcesPath, "backend");
}

function waitForBackend(timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const poll = () => {
      const request = http.get("http://127.0.0.1:8877/api/health", (response) => {
        response.resume();
        if (response.statusCode === 200) {
          resolve();
          return;
        }
        retry();
      });
      request.on("error", retry);
      request.setTimeout(1200, () => {
        request.destroy();
        retry();
      });
    };
    const retry = () => {
      if (Date.now() >= deadline) {
        reject(new Error("本地后端启动超时"));
        return;
      }
      setTimeout(poll, 500);
    };
    poll();
  });
}

function startBackend() {
  if (backendProcess) {
    return;
  }
  const command = isDev
    ? (process.env.ANSWER_STUDIO_PYTHON || "python")
    : path.join(backendDirectory(), "answer-studio-backend.exe");
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
}

async function createWindow() {
  startBackend();
  await waitForBackend();
  mainWindow = new BrowserWindow({
    width: 1480,
    height: 960,
    minWidth: 1180,
    minHeight: 760,
    backgroundColor: "#f3f5f2",
    title: "AI Assignment Studio",
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false
    }
  });

  if (isDev) {
    await mainWindow.loadURL("http://127.0.0.1:5174");
  } else {
    await mainWindow.loadFile(path.join(__dirname, "..", "dist", "index.html"));
  }
}

app.whenReady().then(createWindow);

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

ipcMain.handle("app:open-data-directory", async () => {
  const dataRoot = path.join(
    process.env.LOCALAPPDATA || app.getPath("userData"),
    "AIAssistantStudio"
  );
  const error = await shell.openPath(dataRoot);
  if(error) throw new Error(error);
  return dataRoot;
});

ipcMain.handle("app:open-log-directory", async () => {
  const logsRoot = path.join(
    process.env.LOCALAPPDATA || app.getPath("userData"),
    "AIAssistantStudio",
    "logs"
  );
  const error = await shell.openPath(logsRoot);
  if(error) throw new Error(error);
  return logsRoot;
});
