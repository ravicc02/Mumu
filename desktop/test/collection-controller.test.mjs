import assert from "node:assert/strict";
import { test } from "node:test";
import { createCollectionController } from "../src/features/assignments/lib/collection-controller.js";

function harness(fetchRuns = async () => []) {
  let states = {};
  let next = 0;
  const timers = new Map();
  const controller = createCollectionController({ fetchRuns, changed: value => { states = value; },
    timers: { setTimeout: fn => { timers.set(++next, fn); return next; }, clearTimeout: id => timers.delete(id) } });
  return { controller, timers, get states() { return states; } };
}

test("two accounts collect independently and finishing one keeps the other poll alive", () => {
  const state = harness();
  assert.equal(state.controller.begin("a"), true);
  assert.equal(state.controller.begin("b"), true);
  assert.equal(state.controller.begin("a"), false);
  assert.equal(state.timers.size, 2);
  state.controller.finish("a", { status: "success", submittable_count: 3 });
  assert.equal(state.states.a.pending, false);
  assert.equal(state.states.b.pending, true);
  assert.equal(state.states.a.run.submittable_count, 3);
  assert.equal(state.timers.size, 1);
  state.controller.dispose();
  assert.equal(state.timers.size, 0);
});

test("late progress from an old attempt cannot overwrite a finished account", async () => {
  let resolve;
  const state = harness(() => new Promise(done => { resolve = done; }));
  const request = state.controller.refresh("a");
  await Promise.resolve();
  state.controller.begin("a");
  state.controller.finish("a", { status: "success", progress: 100 });
  resolve([{ status: "running", progress: 20 }]);
  await request;
  assert.equal(state.states.a.run.status, "success");
  assert.equal(state.timers.size, 0);
  state.controller.dispose();
});

test("progress reads coalesce per account while preserving another account's response", async () => {
  const replies = {};
  const state = harness(account => new Promise(resolve => { replies[account] = resolve; }));
  const first = state.controller.refresh("a");
  assert.equal(state.controller.refresh("a"), first);
  const second = state.controller.refresh("b");
  await Promise.resolve();
  replies.b([{ status: "running", current_course: "B" }]);
  await second;
  replies.a([{ status: "success", current_course: "A" }]);
  await first;
  assert.equal(state.states.a.run.current_course, "A");
  assert.equal(state.states.b.run.current_course, "B");
  assert.equal(state.timers.size, 1);
  state.controller.dispose();
});
