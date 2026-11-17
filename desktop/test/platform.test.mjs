import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import path from "node:path";

const { backendExecutable, browserCandidates, desktopExec, createLinuxAutoLaunch } = createRequire(import.meta.url)("../electron/platform.cjs");

test("Linux uses the extensionless bundled backend and discovers supported browsers from PATH", () => {
  assert.equal(backendExecutable({ backendExecutable: "mumu-backend.exe" }, "linux"), "mumu-backend");
  const candidates = browserCandidates({ PATH: "/custom/bin:/usr/bin" }, "linux");
  assert.ok(candidates.includes("/custom/bin/google-chrome"));
  assert.ok(candidates.includes("/usr/bin/chromium"));
  assert.ok(candidates.includes("/snap/bin/microsoft-edge-stable"));
});

test("Linux desktop-entry quoting rejects unsafe paths and preserves spaces", () => {
  assert.equal(desktopExec("/opt/Mumu AppImage/mumu"), '"/opt/Mumu AppImage/mumu"');
  assert.throws(() => desktopExec("relative/mumu"), /路径无效/);
  assert.throws(() => desktopExec("/tmp/mumu\n--flag"), /路径无效/);
});

test("Linux auto-launch writes an atomic desktop entry in the requested config root", async () => {
  const files = new Map();
  const io = {
    readFile: async file => {
      if (!files.has(file)) throw Object.assign(new Error("missing"), { code: "ENOENT" });
      return files.get(file);
    },
    writeFile: async (file, value) => files.set(file, value),
    mkdir: async () => {},
    rename: async (from, to) => { files.set(to, files.get(from)); files.delete(from); },
    unlink: async file => {
      if (!files.delete(file)) throw Object.assign(new Error("missing"), { code: "ENOENT" });
    },
  };
  const launch = createLinuxAutoLaunch({
    env: { XDG_CONFIG_HOME: "/fixture/config", APPIMAGE: "/opt/Mumu AppImage/mumu" },
    executable: "/usr/bin/electron",
    home: "/fixture/home",
    io,
  });
  assert.deepEqual(await launch.get(), { supported: true, enabled: false });
  assert.deepEqual(await launch.set(true), { supported: true, enabled: true });
  const target = path.join("/fixture/config", "autostart", "io.github.ravicc02.mumu.desktop");
  assert.match(files.get(target), /Name=Mumu/);
  assert.match(files.get(target), /Exec="\/opt\/Mumu AppImage\/mumu"/);
  assert.deepEqual(await launch.set(false), { supported: true, enabled: false });
  assert.equal(files.has(target), false);
});
