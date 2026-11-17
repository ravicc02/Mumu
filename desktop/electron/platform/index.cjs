const path = require("node:path");
const os = require("node:os");
const fs = require("node:fs/promises");
const crypto = require("node:crypto");

function executableName(product, platform = process.platform) {
  return product.backendExecutables?.[platform] || (platform === "win32" ? product.backendExecutable : "mumu-backend");
}
function browserCandidates(env = process.env, platform = process.platform) {
  const override = [env.MUMU_CHROME_PATH, env.ANSWER_STUDIO_CHROME_PATH];
  if (platform === "linux") {
    const join = path.posix.join;
    return [...override, ...["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge", "microsoft-edge-stable"].flatMap(name =>
      [...(env.PATH || "/usr/bin:/usr/local/bin").split(":"), "/snap/bin"].filter(Boolean).map(dir => join(dir, name))), "/opt/google/chrome/chrome"].filter(Boolean);
  }
  if (platform === "darwin") return [...override, "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium", "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"].filter(Boolean);
  return [...override,
    path.join(env.ProgramFiles || "C:/Program Files", "Google/Chrome/Application/chrome.exe"),
    path.join(env["ProgramFiles(x86)"] || "C:/Program Files (x86)", "Google/Chrome/Application/chrome.exe"),
    path.join(env.LOCALAPPDATA || "", "Google/Chrome/Application/chrome.exe"),
    path.join(env.ProgramFiles || "C:/Program Files", "Microsoft/Edge/Application/msedge.exe"),
    path.join(env["ProgramFiles(x86)"] || "C:/Program Files (x86)", "Microsoft/Edge/Application/msedge.exe")].filter(Boolean);
}

// Desktop-entry Exec uses its own quoting rules; it is never evaluated by a shell.
function desktopExec(executable) {
  if (!path.isAbsolute(executable) || /[\r\n\0]/.test(executable)) throw new Error("自启动程序路径无效");
  return '"' + executable.replace(/%/g, "%%").replace(/\\/g, "\\\\\\\\").replace(/([`"$])/g, "\\\\$1") + '"';
}
function createLinuxAutoLaunch({env = process.env, executable = process.execPath, home = os.homedir(), io = fs} = {}) {
  const config = env.XDG_CONFIG_HOME && path.isAbsolute(env.XDG_CONFIG_HOME) ? env.XDG_CONFIG_HOME : path.join(home, ".config");
  const target = path.join(config, "autostart", "mumu.desktop");
  const launch = env.APPIMAGE || executable;
  const content = `[Desktop Entry]\nType=Application\nName=Mumu\nExec=${desktopExec(launch)}\nTerminal=false\nX-GNOME-Autostart-enabled=true\n`;
  async function get() {
    try { return {supported:true, enabled:await io.readFile(target, "utf8") === content}; }
    catch (error) { if (error.code === "ENOENT") return {supported:true, enabled:false}; throw error; }
  }
  async function set(enabled) {
    if (typeof enabled !== "boolean") throw new Error("开机自启设置值无效");
    if (!enabled) { try { await io.unlink(target); } catch (error) { if (error.code !== "ENOENT") throw error; } }
    else {
      await io.mkdir(path.dirname(target), {recursive:true});
      const temp = target + "." + crypto.randomUUID() + ".tmp";
      try { await io.writeFile(temp, content, {mode:0o600, flag:"wx"}); await io.rename(temp, target); }
      finally { await io.unlink(temp).catch(error => { if (error.code !== "ENOENT") throw error; }); }
    }
    return get();
  }
  return {get, set};
}
module.exports = {executableName, browserCandidates, desktopExec, createLinuxAutoLaunch};
