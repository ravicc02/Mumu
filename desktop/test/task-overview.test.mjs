import { test } from "node:test";
import assert from "node:assert/strict";
import { buildTaskOverview, overviewResult } from "../src/lib/task-overview.js";

test("the dashboard retains one latest attempt per account and business object across both task types", () => {
  const jobs = [{ id: "assignment", account_id: "a", assignment_id: "same", status: "running", created_at: "2026-11-11T10:00:00Z" }];
  const runs = [
    { id: "old", account_id: "a", course_id: "same", status: "interrupted", created_at: "2026-11-11T09:00:00Z", updated_at: "2026-11-11T12:00:00Z" },
    { id: "latest", account_id: "a", course_id: "same", status: "awaiting_user", created_at: "2026-11-11T11:00:00Z", course_name: "WPS" },
    { id: "other-account", account_id: "b", course_id: "same", status: "completed", created_at: "2026-11-11T10:30:00Z" },
  ];
  const tasks = buildTaskOverview(jobs, runs);
  assert.equal(tasks.length, 3);
  assert.ok(!tasks.some(task => task.id === "old"));
  const learning = tasks.find(task => task.id === "latest");
  assert.equal(learning.status, "needs_intervention");
  assert.equal(learning.assignment_title, "WPS");
  assert.equal(runs[1].status, "awaiting_user");
  assert.equal(new Set(tasks.map(task => task.task_key)).size, 3);
});

test("learning results show course progress and never borrow assignment submission labels", () => {
  const noSubmission = () => { throw new Error("learning must not show assignment submission state"); };
  assert.equal(overviewResult({ task_kind: "learning", status: "completed" }, noSubmission), "平台进度已核验");
  assert.equal(overviewResult({ task_kind: "learning", status: "needs_intervention", error: "需要处理练习" }, noSubmission), "需要处理练习");
  assert.equal(overviewResult({ task_kind: "learning", status: "stopped" }, noSubmission), "用户已停止");
  assert.equal(overviewResult({ task_kind: "assignment", submission_status: "filled" }, value => value), "filled");
});
