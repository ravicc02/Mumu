import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const source = await readFile(new URL("../electron/main.cjs", import.meta.url), "utf8");
const preload = await readFile(new URL("../electron/preload.cjs", import.meta.url), "utf8");

test("Windows application shell does not expose a native menu", () => {
  assert.match(source, /Menu\.setApplicationMenu\(null\)/);
  assert.match(source, /mainWindow\.setMenuBarVisibility\(false\)/);
});

test("geolocation permission is limited to the application window", () => {
  assert.match(source, /setPermissionCheckHandler\(\(webContents, permission\)/);
  assert.match(source, /setPermissionRequestHandler\(\(webContents, permission, callback\)/);
  assert.match(source, /permission === "geolocation" && webContents === mainWindow\.webContents/);
  assert.match(source, /app:get-windows-location/);
  assert.match(source, /readWindowsLocation/);
  assert.match(source, /app:open-location-settings/);
  assert.match(source, /ms-settings:privacy-location/);
});

test("auto launch is a restricted packaged Windows setting", () => {
  assert.match(source, /app:get-auto-launch/);
  assert.match(source, /app:set-auto-launch/);
  assert.match(source, /app\.setLoginItemSettings/);
  assert.match(source, /app\.isPackaged/);
  assert.match(source, /process\.platform === "win32"/);
  assert.match(preload, /getAutoLaunch/);
  assert.match(preload, /setAutoLaunch/);
});

test("closing the main window keeps Mumu running in the tray", () => {
  assert.match(source, /requestSingleInstanceLock/);
  assert.match(source, /new Tray\(/);
  assert.match(source, /label: "打开 Mumu"/);
  assert.match(source, /label: "退出 Mumu"/);
  assert.match(source, /mainWindow\.on\("close", event =>/);
  assert.match(source, /event\.preventDefault\(\)/);
  assert.match(source, /mainWindow\.hide\(\)/);
  assert.match(source, /quitting = true/);
  assert.match(source, /window-all-closed[\s\S]*?if \(!tray/);
});
