import { test } from "node:test";
import assert from "node:assert/strict";
import { TOWN_DISTRICTS, townDistrict, townTaskKey, townTaskActions, BOT_SHAPES, botShape } from "../src/features/task-town/lib/model.js";
import { companyLayout } from "../src/features/task-town/lib/company.js";
import { buildTaskOverview } from "../src/features/tasks/lib/overview.js";

test("every task state has a semantic district, including queued, stopped and legacy errors", () => {
  const expected = { queued: "active", running: "active", stopping: "active", needs_intervention: "review", awaiting_user: "review", completed: "completed", interrupted: "interrupted", failed: "interrupted", incomplete: "incomplete", paused: "paused", stopped: "paused" };
  for (const [status, district] of Object.entries(expected)) assert.equal(townDistrict({ status }), district);
  assert.equal(new Set(TOWN_DISTRICTS.map(district => district.color)).size, 6);
});

test("retrying a task retains its resident identity and moves to the new district", () => {
  const previous = { id: "old", account_id: "one", assignment_id: "work", status: "interrupted", created_at: "2026-11-11" };
  const retry = { ...previous, id: "new", status: "running", created_at: "2026-11-12" };
  const before = companyLayout(buildTaskOverview([previous], [])).residents;
  const after = companyLayout(buildTaskOverview([previous, retry], [])).residents;
  assert.equal(after.length, 1);
  assert.equal(before[0].key, after[0].key);
  assert.notEqual(before[0].district, after[0].district);
  assert.notEqual(before[0].color, after[0].color);
});

test("company resident slots remain deterministic and inside the expanded office", () => {
  const tasks = Array.from({ length: 150 }, (_, index) => ({ id: String(index), account_id: "a", assignment_id: "work-" + index, status: "running" }));
  const layout = companyLayout(tasks);
  const first = layout.residents, reordered = companyLayout([...tasks].reverse()).residents;
  assert.deepEqual(first.map(({ key, x, z }) => ({ key, x, z })), reordered.map(({ key, x, z }) => ({ key, x, z })));
  assert.equal(new Set(first.map(item => item.x + ":" + item.z)).size, tasks.length);
  assert.ok(first.every(item => item.x > layout.bounds.left && item.x < layout.bounds.right && item.z < layout.bounds.front));
});

test("all twelve bot shapes are available and task identity preserves its shape", () => {
  assert.equal(BOT_SHAPES.length, 12);
  assert.equal(new Set(Array.from({length:500}, (_,index) => botShape("task-" + index))).size, 12);
  assert.equal(botShape("same-task"), botShape("same-task"));
});

test("accounts and task types never share a character identity", () => {
  const assignment = { account_id: "one", assignment_id: "same", task_kind: "assignment" };
  assert.notEqual(townTaskKey(assignment), townTaskKey({ ...assignment, account_id: "two" }));
  assert.notEqual(townTaskKey(assignment), townTaskKey({ account_id: "one", course_id: "same", task_kind: "learning" }));
});

test("town task cards preserve server-authorized deletion and historical course result entry", () => {
  assert.deepEqual(townTaskActions({ status: "running", task_kind: "assignment", actions: ["pause"] }), { detailsLabel: "查看任务", deletable: false });
  assert.deepEqual(townTaskActions({ status: "completed", task_kind: "learning", actions: ["delete"] }), { detailsLabel: "查看结果", deletable: true });
});
