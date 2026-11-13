import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const source = await readFile(new URL("../electron/main.cjs", import.meta.url), "utf8");

test("Windows application shell does not expose a native menu", () => {
  assert.match(source, /Menu\.setApplicationMenu\(null\)/);
  assert.match(source, /mainWindow\.setMenuBarVisibility\(false\)/);
});

test("geolocation permission is limited to the application window", () => {
  assert.match(source, /setPermissionCheckHandler\(\(webContents, permission\)/);
  assert.match(source, /setPermissionRequestHandler\(\(webContents, permission, callback\)/);
  assert.match(source, /permission === "geolocation" && webContents === mainWindow\.webContents/);
});
