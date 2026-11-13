import { test } from "node:test";
import assert from "node:assert/strict";
import { monitorSummary, newMonitorState, reconcileMonitor, restoreMonitorState } from "../src/features/tasks/lib/monitor.js";

const task = (id, status = "queued", extra = {}) => ({ id, task_key: "assignment:a:" + id, task_kind: "assignment", account_id: "a", assignment_id: id,
  assignment_title: id, status, submission_status: status === "completed" ? "submitted_verified" : "not_started", created_at: "2026-11-12T00:00:00Z", updated_at: "2026-11-12T00:00:00Z", ...extra });
const begin = records => reconcileMonitor(newMonitorState(), records);

test("historical errors and reviews do not become the current round on startup", () => {
  const state = begin([task("old-error", "failed"), task("old-review", "needs_intervention"), task("old-complete", "completed")]);
  const summary = monitorSummary(state);
  assert.equal(summary.mood, "idle");
  assert.equal(summary.total, 0);
  assert.equal(summary.water, .09);
});

test("five real tasks with two verified completions fill exactly two fifths", () => {
  const initial = Array.from({ length: 5 }, (_, index) => task("task-" + index, index === 0 ? "running" : "queued"));
  let state = begin(initial);
  const current = initial.map((item, index) => index < 2 ? { ...item, status: "completed", submission_status: "submitted_verified", updated_at: "2026-11-12T00:01:00Z" } : item);
  state = reconcileMonitor(state, current);
  const summary = monitorSummary(state);
  assert.equal(summary.total, 5);
  assert.equal(summary.completed, 2);
  assert.equal(summary.water, .4);
  assert.equal(summary.percent, 40);
  assert.equal(summary.mood, "working");
});

test("filled drafts, reviews, pauses and stops cannot impersonate completion", () => {
  let state = begin([task("one"), task("two"), task("three"), task("four")]);
  state = reconcileMonitor(state, [task("one", "needs_intervention"), task("two", "completed", { submission_status: "submitted_unverified" }), task("three", "paused"), task("four", "stopped")]);
  const summary = monitorSummary(state);
  assert.equal(summary.completed, 0);
  assert.equal(summary.water, 0);
  assert.equal(summary.review, 2);
  assert.notEqual(summary.mood, "happy");
});

test("multiple current errors show anger while preserving honest completed water", () => {
  let state = begin([task("one"), task("two"), task("three")]);
  state = reconcileMonitor(state, [task("one", "completed"), task("two", "failed"), task("three", "incomplete")]);
  assert.equal(monitorSummary(state).mood, "angry");
  assert.equal(monitorSummary(state).water, 1 / 3);
  assert.equal(monitorSummary(state).errors, 2);
});

test("a single failed task that cannot proceed is blocked, not reported as done", () => {
  const state = reconcileMonitor(begin([task("one")]), [task("one", "interrupted")]);
  assert.equal(monitorSummary(state).mood, "angry");
  assert.equal(monitorSummary(state).completed, 0);
});

test("all verified completions, including courses, fill the sphere completely", () => {
  const state = reconcileMonitor(begin([task("one"), task("course", "running", { task_kind: "learning" })]), [task("one", "completed"), task("course", "completed", { task_kind: "learning", submission_status: undefined })]);
  assert.equal(monitorSummary(state).mood, "happy");
  assert.equal(monitorSummary(state).water, 1);
});

test("retrying the same assignment replaces its attempt without growing the denominator", () => {
  let state = reconcileMonitor(begin([task("work")]), [task("work", "failed")]);
  const retry = task("retry", "running", { task_key: "assignment:a:work", assignment_id: "work", created_at: "2026-11-12T00:02:00Z" });
  state = reconcileMonitor(state, [retry]);
  assert.equal(monitorSummary(state).total, 1);
  assert.equal(monitorSummary(state).errors, 0);
  assert.equal(monitorSummary(state).mood, "working");
  assert.equal(monitorSummary(state).entries[0].id, "retry");
});

test("a new independent batch starts fresh after a previous batch finishes", () => {
  let state = reconcileMonitor(begin([task("old")]), [task("old", "completed")]);
  state = reconcileMonitor(state, [task("old", "completed"), task("new", "running")]);
  assert.equal(monitorSummary(state).total, 1);
  assert.equal(monitorSummary(state).completed, 0);
});

test("persisted progress survives reload, stale responses and network outages", () => {
  let state = reconcileMonitor(begin([task("one"), task("two")]), [task("one", "completed", { updated_at: "2026-11-12T00:02:00Z" }), task("two", "paused")]);
  state = restoreMonitorState(JSON.parse(JSON.stringify(state)));
  state = reconcileMonitor(state, [task("one"), task("two", "paused")]);
  assert.equal(monitorSummary(state).completed, 1);
  const offline = monitorSummary(state, { online: false });
  assert.equal(offline.water, .5);
  assert.equal(offline.label, "连接中断");
  assert.notEqual(offline.mood, "happy");
});

test("slow polls and invalid storage do not manufacture a batch", () => {
  const initial = newMonitorState();
  assert.equal(reconcileMonitor(initial, [task("one")], { ready: false }), initial);
  assert.deepEqual(restoreMonitorState({ version: 9, seen: {} }), initial);
  assert.deepEqual(restoreMonitorState({ version: 1, seen: [] }), initial);
  assert.equal(monitorSummary(initial, { ready: false }).label, "同步中");
});

test("persisted task descriptions omit signed links and internal diagnostics", () => {
  const failed = task("one", "failed", { error: "页面填写与核验失败：https://example.test/?token=private" });
  const state = reconcileMonitor(begin([task("one")]), [failed]);
  assert.ok(!JSON.stringify(state.round).includes("private"));
});

test("larger batches retain every task in the denominator", () => {
  const tasks = Array.from({ length: 40 }, (_, index) => task(String(index)));
  const state = reconcileMonitor(begin(tasks), tasks.map((item, index) => index < 32 ? { ...item, status: "completed", submission_status: "submitted_verified" } : item));
  const summary = monitorSummary(state);
  assert.equal(summary.total, 40);
  assert.equal(summary.completed, 32);
  assert.equal(summary.water, .8);
  assert.equal(summary.entries.length, 40);
  assert.notEqual(summary.mood, "happy");
});
