import { test } from "node:test";
import assert from "node:assert/strict";
import { api } from "../src/api.js";
import { newMonitorState, reconcileMonitor, monitorSummary } from "../src/lib/task-monitor.js";

test("deleting each task type calls its local DELETE endpoint", async () => {
  const previous = globalThis.fetch, requests = [];
  globalThis.fetch = async (url, options) => { requests.push({ url, options }); return { ok: true, json: async () => ({ deleted_ids: ["task"] }) }; };
  try {
    await api.deleteTask({ id: "task", task_kind: "assignment" });
    await api.deleteTask({ id: "course-task", task_kind: "learning" });
    assert.equal(requests[0].url, "http://127.0.0.1:8877/api/jobs/task");
    assert.equal(requests[1].url, "http://127.0.0.1:8877/api/learning/runs/course-task");
    assert.ok(requests.every(item => item.options.method === "DELETE"));
  } finally { globalThis.fetch = previous; }
});

test("a deleted task leaves the active monitor round and a late poll cannot restore it", () => {
  const old = { id: "old", account_id: "a", assignment_id: "work", status: "running", created_at: "2026-11-12T00:00:00Z" };
  let state = reconcileMonitor(newMonitorState(), [old]);
  state = reconcileMonitor(state, [old], { removedIds: ["old"] });
  assert.equal(monitorSummary(state).total, 0);
  assert.equal(Object.keys(state.seen).length, 0);
  const next = { ...old, id: "new", created_at: "2026-11-12T01:00:00Z" };
  state = reconcileMonitor(state, [old, next], { removedIds: ["old"] });
  assert.equal(monitorSummary(state).total, 1);
  assert.equal(monitorSummary(state).entries[0].id, "new");
});
