import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { copyText } from "../src/app/lib/clipboard.js";
const require = createRequire(import.meta.url);
const { registerClipboard } = require("../electron/clipboard.cjs");

function fixture(success = true) {
  const state = { removed: false, focused: false, restored: false, selected: false, value: "", ranges: [] };
  const range = { cloneRange: () => range };
  const selection = { rangeCount: 1, getRangeAt: () => range, removeAllRanges: () => { state.ranges = []; }, addRange: value => state.ranges.push(value) };
  const active = { selectionStart: 2, selectionEnd: 4, selectionDirection: "forward", focus: () => { state.focused = true; }, setSelectionRange: (...values) => { state.restored = values; } };
  const field = { style: {}, setAttribute() {}, focus() {}, select: () => { state.selected = true; }, remove: () => { state.removed = true; } };
  const document = { activeElement: active, getSelection: () => selection, createElement: tag => { assert.equal(tag, "textarea"); return field; }, body: { appendChild: value => { state.value = value.value; } }, execCommand: command => { assert.equal(command, "copy"); return success; } };
  return { document, state, range };
}
test("desktop clipboard is preferred without browser permission", async () => {
  let written;
  await copyText("链接", { desktop: { writeClipboardText: async value => { written = value; } }, navigator: { clipboard: { writeText: () => assert.fail("不应使用浏览器") } } });
  assert.equal(written, "链接");
});
test("browser clipboard works when desktop channel is absent or rejects", async () => {
  for (const desktop of [{}, { writeClipboardText: async () => { throw new Error("IPC unavailable"); } }]) {
    let written;
    await copyText("https://example.test/扫码", { desktop, navigator: { clipboard: { writeText: async value => { written = value; } } } });
    assert.equal(written, "https://example.test/扫码");
  }
});
test("legacy copy restores focus and selection after permission rejection", async () => {
  const { document, state, range } = fixture();
  await copyText("链接", { desktop: {}, navigator: { clipboard: { writeText: async () => { throw new Error("NotAllowedError"); } } }, document });
  assert.equal(state.value, "链接");
  assert.equal(state.selected, true);
  assert.equal(state.removed, true);
  assert.equal(state.focused, true);
  assert.deepEqual(state.restored, [2, 4, "forward"]);
  assert.deepEqual(state.ranges, [range]);
});
test("legacy copy is available without Clipboard API and never reports false success", async () => {
  const available = fixture();
  await copyText("链接", { desktop: {}, navigator: {}, document: available.document });
  const failed = fixture(false);
  await assert.rejects(copyText("链接", { desktop: {}, navigator: {}, document: failed.document }), /无法复制/);
  assert.equal(failed.state.removed, true);
  assert.equal(failed.state.focused, true);
  await assert.rejects(copyText("链接", { desktop: {}, navigator: {}, document: {} }), /无法复制/);
  await assert.rejects(copyText(null), /必须为文本/);
});
test("native clipboard accepts only main frame text and exposes no read operation", () => {
  let handler, written;
  const mainFrame = {}, webContents = { mainFrame }, window = { webContents };
  registerClipboard({ handle: (channel, callback) => { assert.equal(channel, "app:write-clipboard-text"); handler = callback; } }, { writeText: value => { written = value; } }, () => window);
  const event = { sender: webContents, senderFrame: mainFrame };
  assert.equal(handler(event, "链接"), true);
  assert.equal(written, "链接");
  for (const denied of [{ sender: {}, senderFrame: mainFrame }, { sender: webContents, senderFrame: {} }]) assert.throws(() => handler(denied, "错误"), /来源无效/);
  for (const value of [null, {}, "a".repeat(1_000_001)]) assert.throws(() => handler(event, value), /无效或过长/);
  assert.equal(written, "链接");
});
