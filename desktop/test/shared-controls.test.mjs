import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const read = path => readFile(new URL(path, import.meta.url), "utf8");

test("shared state controls expose the P0 status and accessibility contracts", async () => {
  const [button, badge, tooltip, modal] = await Promise.all([
    read("../src/shared/controls/StatefulButton.vue"),
    read("../src/shared/controls/StatusBadge.vue"),
    read("../src/shared/controls/Tooltip.vue"),
    read("../src/shared/controls/ModalShell.vue"),
  ]);
  assert.match(button, /status: \{ type: String, default: "idle"/);
  assert.match(button, /aria-busy/);
  assert.match(button, /emit\("retry"/);
  assert.match(badge, /needs_intervention/);
  assert.match(badge, /completed: \{ label: "已完成"/);
  assert.match(tooltip, /role="tooltip"/);
  assert.match(tooltip, /@focusin="show"/);
  assert.match(modal, /role="dialog"/);
  assert.match(modal, /aria-modal="true"/);
  assert.match(modal, /event\.key !== "Tab"/);
  assert.match(modal, /restoreFocus/);
});

test("existing shared controls retain native semantics while accepting disabled states", async () => {
  const [tabs, toggle, choice, resize] = await Promise.all([
    read("../src/shared/controls/Tabs.vue"),
    read("../src/shared/controls/ToggleSwitch.vue"),
    read("../src/shared/controls/ChoiceInput.vue"),
    read("../src/shared/controls/ResizeHandle.vue"),
  ]);
  assert.match(tabs, /role="tablist"/);
  assert.match(tabs, /role="tab"/);
  assert.match(tabs, /item\.disabled/);
  assert.match(toggle, /role="switch"/);
  assert.match(choice, /type="checkbox"/);
  assert.match(resize, /role="separator"/);
  assert.match(resize, /event\.key === "Escape"/);
});

test("high-frequency pages consume shared status feedback without replacing business state", async () => {
  const [platforms, monitor] = await Promise.all([
    read("../src/features/platforms/components/PlatformWorkspace.vue"),
    read("../src/features/tasks/components/TaskMonitor.vue"),
  ]);
  assert.match(platforms, /StatusBadge/);
  assert.match(platforms, /StatefulButton/);
  assert.match(platforms, /badgeStatus/);
  assert.match(monitor, /StatusBadge/);
  assert.match(monitor, /statusLabel\(task\) === "已完成"/);
  assert.match(monitor, /awaiting_user/);
});
