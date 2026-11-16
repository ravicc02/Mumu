const path = require("node:path");
const fs = require("node:fs");
const os = require("node:os");
const { execFileSync } = require("node:child_process");

function executableName(product, platform = process.platform) {
  return product.backendExecutables?.[platform] || (platform === "win32" ? product.backendExecutable : "mumu-backend");
}

function browserCandidates(env = process.env, platform = process.platform) {
  const configured = [env.MUMU_CHROME_PATH, env.ANSWER_STUDIO_CHROME_PATH].filter(Boolean);
  if (platform === "win32") {
    return [...configured,
      path.join(env.ProgramFiles || "C:/Program Files", "Google/Chrome/Application/chrome.exe"),
      path.join(env["ProgramFiles(x86)"] || "C:/Program Files (x86)", "Google/Chrome/Application/chrome.exe"),
      path.join(env.LOCALAPPDATA || "", "Google/Chrome/Application/chrome.exe"),
      path.join(env.ProgramFiles || "C:/Program Files", "Microsoft/Edge/Application/msedge.exe"),
      path.join(env["ProgramFiles(x86)"] || "C:/Program Files (x86)", "Microsoft/Edge/Application/msedge.exe")].filter(Boolean);
  }
  if (platform === "linux") {
    return [...configured, "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
      "/opt/google/chrome/chrome", "microsoft-edge", "microsoft-edge-stable"];
  }
  if (platform === "darwin") {
    return [...configured, "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
      "/Applications/Chromium.app/Contents/MacOS/Chromium",
      "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"];
  }
  return configured;
}

function resolveBrowserCandidate(candidate, platform = process.platform) {
  if (!candidate) return "";
  if (path.isAbsolute(candidate)) return fs.existsSync(candidate) ? candidate : "";
  if (platform === "win32") return fs.existsSync(candidate) ? candidate : "";
  try { return execFileSync("which", [candidate], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim(); }
  catch { return ""; }
}

function findBrowser(env = process.env, platform = process.platform) {
  for (const candidate of browserCandidates(env, platform)) {
    const resolved = resolveBrowserCandidate(candidate, platform);
    if (resolved) return resolved;
  }
  return "";
}

function autoLaunchFile(product, home = os.homedir()) {
  return path.join(home, ".config", "autostart", `${product.slug || "mumu"}.desktop`);
}

function linuxAutoLaunch(product, enabled, { home = os.homedir(), execPath = process.execPath } = {}) {
  if (process.platform !== "linux") return { supported: false, enabled: false };
  const target = autoLaunchFile(product, home);
  if (!enabled) {
    try { fs.unlinkSync(target); } catch (error) { if (error.code !== "ENOENT") throw error; }
    return { supported: true, enabled: false };
  }
  fs.mkdirSync(path.dirname(target), { recursive: true, mode: 0o700 });
  const command = `"${String(execPath).replaceAll('"', '\\"')}"`;
  fs.writeFileSync(target, `[Desktop Entry]\nType=Application\nName=${product.name}\nExec=${command}\nTerminal=false\nX-GNOME-Autostart-enabled=true\n`, { mode: 0o600 });
  return { supported: true, enabled: true };
}

function linuxAutoLaunchState(product, { home = os.homedir() } = {}) {
  if (process.platform !== "linux") return { supported: false, enabled: false };
  return { supported: true, enabled: fs.existsSync(autoLaunchFile(product, home)) };
}

module.exports = { executableName, browserCandidates, findBrowser, autoLaunchFile, linuxAutoLaunch, linuxAutoLaunchState };
