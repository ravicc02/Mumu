import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { completionSnapshot, newlyCompleted } from "../src/features/brand/lib/droplet.js";
import { contrast, loadTokens, resolve } from "./helpers/tokens.mjs";

const read = path => readFile(new URL(path, import.meta.url), "utf8");

test("完成水滴只在任务从未完成变为已验证完成时触发", () => {
  const before = completionSnapshot([
    { id: "a", status: "running" },
    { id: "b", status: "completed", submission_status: "submitted_verified" },
    { id: "c", status: "running" },
    { id: "d", status: "running", task_kind: "learning" },
  ]);
  const after = [
    { id: "a", status: "completed", submission_status: "submitted_verified" },
    { id: "b", status: "completed", submission_status: "submitted_verified" },
    { id: "c", status: "completed", submission_status: "unknown" },
    { id: "d", status: "completed", task_kind: "learning" },
    { id: "e", status: "completed", submission_status: "submitted_verified" },
  ];
  assert.deepEqual(newlyCompleted(before, after).map(job => job.id), ["a", "d"]);
});

test("作业页使用头像栏和可收起的画面与日志抽屉", async () => {
  const [app, styles, resize] = await Promise.all([read("../src/app/App.vue"), read("../src/styles/workbench.css"), read("../src/shared/controls/ResizeHandle.vue")]);
  assert.match(app, /'inspector-open': assignmentWorkspaceView === 'list' && inspectorOpen/);
  assert.match(app, /v-show="inspectorOpen && \(inspectorTab === 'browser' \|\| browserExpanded\)"/);
  assert.match(app, /:aria-label="assignmentAccountHint\(account\)"/);
  assert.match(styles, /\.platform-layout \{[^}]*grid-template-columns: 64px minmax\(0,1fr\)/);
  assert.doesNotMatch(styles, /\.app-body \{ grid-template-columns: 196px/);
  assert.match(resize, /props\.invert \? -1 : 1/);
});

test("总控中心待处理列表只定位任务，不执行业务动作", async () => {
  const town = await read("../src/features/task-town/components/TaskTown.vue");
  const list = town.slice(town.indexOf('class="town-attention"'), town.indexOf("</aside>", town.indexOf('class="town-attention"')));
  assert.match(list, /@click="choose\(task\)"/);
  assert.doesNotMatch(list, /emit\(|api\./);
});

test("新主色在白底上满足 4.5:1 对比度", async () => {
  const { light } = await loadTokens();
  assert.ok(contrast(resolve("--ds-brand-primary", light), [255, 255, 255]) >= 4.5);
});
