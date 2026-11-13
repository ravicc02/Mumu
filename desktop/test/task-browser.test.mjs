import { test } from "node:test";
import assert from "node:assert/strict";
import { assignmentBrowserTask, activeAssignmentBrowserTask } from "../src/lib/task-browser.js";

test("the chosen assignment keeps its own browser even when another task is running", () => {
  const jobs = [ { id: "one", account_id: "a", browser_available: true, status: "running" }, { id: "two", account_id: "a", browser_available: true, status: "running" } ];
  assert.equal(assignmentBrowserTask(jobs, "a", "two").id, "two");
  assert.equal(assignmentBrowserTask(jobs, "a", "one").id, "one");
  assert.equal(assignmentBrowserTask(jobs, "other", "one"), null);
});

test("a selected historical result cannot be replaced by an unrelated live task", () => {
  const jobs = [{ id: "active", account_id: "a", browser_available: true, status: "running" }, { id: "old", account_id: "a", status: "needs_intervention" }];
  const selected = assignmentBrowserTask(jobs, "a", "old");
  assert.equal(selected.id, "old");
  assert.equal(activeAssignmentBrowserTask(selected), null);
  assert.equal(assignmentBrowserTask(jobs, "a", "missing").id, "active");
});
