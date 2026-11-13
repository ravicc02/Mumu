import { test } from "node:test";
import assert from "node:assert/strict";
import { learningResultSummary, learningScreenshotSections, loadLearningResult } from "../src/lib/learning-result.js";
import { api } from "../src/api.js";

test("historical course results load the requested run without depending on the current course list", async () => {
  const calls = [];
  const result = await loadLearningResult({
    learningRun: async id => { calls.push(["run", id]); return { id, status: "completed", phase: "verified", target: ["one"], completed: ["one"] }; },
    learningEvents: async id => { calls.push(["events", id]); return [{ id: 7, kind: "completed", message: "全部章节已通过平台复验" }]; },
    learningArtifacts: async id => { calls.push(["artifacts", id]); return [{ id: "image", kind: "screenshot" }]; },
    learningArtifactUrl: id => "/local/learning/" + id,
  }, "historical-attempt");
  assert.deepEqual(calls, [["run", "historical-attempt"], ["events", "historical-attempt"], ["artifacts", "historical-attempt"]]);
  assert.equal(result.summary.verified, true);
  assert.equal(result.sections[0].src, "/local/learning/image");
  assert.deepEqual([result.summary.completed, result.summary.total], [1, 1]);
});

test("saved screenshots remain segmented and result JSON never becomes an image", () => {
  const artifacts = [{ id: "a", kind: "screenshot" }, { id: "report", kind: "result" }, { id: "b", kind: "screenshot" }];
  assert.deepEqual(learningScreenshotSections(artifacts, id => "/" + id), [
    { id: "a", src: "/a", index: 1, count: 2 }, { id: "b", src: "/b", index: 2, count: 2 },
  ]);
});

test("a high progress value or a playback event cannot replace platform verification", () => {
  assert.equal(learningResultSummary({ status: "running", progress: 100 }, [{ kind: "media_started" }]).verified, false);
  assert.equal(learningResultSummary({ status: "completed", phase: "learning" }).verified, false);
  const result = learningResultSummary({ status: "completed", phase: "verified", target: ["a", "a", "b"], completed: ["a", "a", "foreign"] });
  assert.deepEqual([result.completed, result.total], [1, 2]);
});

test("a historical run without screenshots still exposes its result and verification logs", async () => {
  const result = await loadLearningResult({ learningRun: async () => ({ id: "old", status: "completed", phase: "verified" }), learningEvents: async () => [{ id: 1, kind: "completed", message: "已核验" }], learningArtifacts: async () => [], learningArtifactUrl: id => id }, "old");
  assert.equal(result.summary.verified, true);
  assert.equal(result.sections.length, 0);
  assert.equal(result.summary.checkpoints.length, 1);
});

test("a mismatched result is rejected instead of showing another task's screenshots", async () => {
  await assert.rejects(loadLearningResult({ learningRun: async () => ({ id: "other" }), learningEvents: async () => [], learningArtifacts: async () => [], learningArtifactUrl: id => id }, "requested"), /当前任务不一致/);
});

test("the individual learning result API encodes the run identifier", async () => {
  const previous = globalThis.fetch, calls = [];
  globalThis.fetch = async url => { calls.push(url); return { ok: true, json: async () => ({}) }; };
  try { await api.learningRun("a/b"); assert.equal(calls[0], "http://127.0.0.1:8877/api/learning/runs/a%2Fb"); }
  finally { globalThis.fetch = previous; }
});
