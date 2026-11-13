import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import { api } from "../src/api.js";
import { clamp, resistEdge, stepSpring, pillFlightFrames } from "../src/components/leibox/motion.js";
import { buildWorkflowStages, canJobAction, mergeJobState, reconcileEvents } from "../src/lib/job-state.js";
import { splitScreenshot, finalScreenshotSections, readableScreenshotZoom } from "../src/lib/screenshots.js";

const originalFetch = globalThis.fetch;

test("log directory opens through the local service without sending a path", async () => {
  globalThis.fetch = async (url, options) => {
    assert.equal(url, "http://127.0.0.1:8877/api/system/open-log-directory");
    assert.equal(options.method, "POST");
    assert.equal(options.body, undefined);
    return new Response(JSON.stringify({ opened: true }));
  };
  assert.equal((await api.openLogDirectory()).opened, true);
});

test("an old backend explains when the directory opener can be enabled", async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({ detail: "Not Found" }), { status: 404 });
  await assert.rejects(api.openLogDirectory(), /任务结束后重启本地服务/);
});

test("an older running backend explains that diagnostics activate after a safe restart", async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({ detail: "Not Found" }), { status: 404 });
  await assert.rejects(api.diagnosticInfo("assignment", "task-one"), /任务结束后重启应用/);
});

test("diagnostic export downloads a server-built ZIP with explicit content choices", async () => {
  globalThis.fetch = async (url, options) => {
    assert.equal(url, "http://127.0.0.1:8877/api/diagnostics/learning/run-one/export");
    assert.equal(options.method, "POST");
    assert.deepEqual(JSON.parse(options.body), { include_content: true, include_screenshots: false });
    return new Response(new Uint8Array([80,75,3,4]), { headers: { "Content-Type": "application/zip" } });
  };
  const result = await api.exportDiagnostics("learning", "run-one", { include_content: true, include_screenshots: false });
  assert.equal(result.filename, "diagnostic-learning-run-one.zip");
  assert.equal(result.blob.size, 4);
});

test("diagnostic export reports unsupported responses instead of saving JSON as a ZIP", async () => {
  globalThis.fetch = async () => new Response("{}", { headers: { "Content-Type": "application/json" } });
  await assert.rejects(api.exportDiagnostics("assignment", "task-one"), /格式不正确/);
  globalThis.fetch = async () => new Response(JSON.stringify({ detail: "任务不存在" }), { status: 404 });
  await assert.rejects(api.exportDiagnostics("assignment", "missing"), /任务不存在/);
});

test("screenshots use readable sizing in narrow panes and fit naturally in expanded panes", () => {
  assert.equal(readableScreenshotZoom(2139, 523), 250);
  assert.equal(readableScreenshotZoom(2139, 1400), 100);
  assert.equal(readableScreenshotZoom(600, 900), 100);
  assert.equal(readableScreenshotZoom(2139, 0), 100);
});

test("learning history supports all-account overview and encodes an optional account filter", async () => {
  const requests = [];
  globalThis.fetch = async url => { requests.push(url); return new Response("[]"); };
  await api.learningRuns();
  await api.learningRuns("account one");
  assert.deepEqual(requests, ["http://127.0.0.1:8877/api/learning/runs", "http://127.0.0.1:8877/api/learning/runs?account_id=account%20one"]);
});

test("learning tasks use our local API and only send the selected course identity", async () => {
  globalThis.fetch = async (url, options) => {
    assert.equal(url, "http://127.0.0.1:8877/api/learning/runs");
    assert.equal(options.method, "POST");
    assert.deepEqual(JSON.parse(options.body), { course_id: "course-one" });
    return new Response(JSON.stringify({ id: "learning-one", status: "queued" }));
  };
  assert.equal((await api.createLearningRun("course-one")).status, "queued");
});

test("legacy long screenshots split into bounded overlapping pages with the full tail", () => {
  const pages = splitScreenshot(1440, 12733);
  assert.ok(pages.length > 10);
  assert.equal(pages[0].y, 0);
  assert.equal(pages.at(-1).y + pages.at(-1).height, 12733);
  assert.ok(pages.every(page => page.height <= 1000));
  assert.equal(pages[0].height - pages[1].y, 64);
  assert.equal(splitScreenshot(1440, 700).length, 1);
  assert.deepEqual(splitScreenshot(0, 100), []);
});

test("screenshot gallery uses only the latest capture and waits for real artifacts", () => {
  const events = [
    { kind: "final_screenshot", data: { segments: [{ artifact_id: "old", index: 1 }] } },
    { kind: "final_screenshot", data: { segments: [{ artifact_id: "new", index: 1 }, { artifact_id: "missing", index: 2 }] } },
  ];
  assert.deepEqual(finalScreenshotSections(events, [{ id: "old" }, { id: "new" }]).map(item => item.id), ["new"]);
});

test("saving has explicit progress and verified draft feedback stays in manual review", () => {
  assert.match(buildWorkflowStages({ status: "running", phase: "saving" }, [])[5].detail, /暂时保存/);
  const review = buildWorkflowStages({ status: "needs_intervention" }, [{ kind: "draft_saved" }])[5];
  assert.equal(review.state, "attention");
  assert.match(review.detail, /已暂存/);
});
afterEach(() => { globalThis.fetch = originalFetch; });

test("network failure explains the local service instead of leaking fetch jargon", async () => {
  globalThis.fetch = async () => { throw new TypeError("Failed to fetch"); };
  await assert.rejects(api.health(), /无法连接本地服务（8877）/);
});

test("server validation errors remain readable", async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({ detail: [{ msg: "名称不能为空" }] }), { status: 422 });
  await assert.rejects(api.createAccount(""), /名称不能为空/);
});

test("request bodies and content type survive action dispatch", async () => {
  globalThis.fetch = async (url, options) => {
    assert.equal(options.method, "POST");
    assert.equal(options.headers["Content-Type"], "application/json");
    assert.deepEqual(JSON.parse(options.body), { name: "Gym", platform: "chaoxing" });
    return new Response(JSON.stringify({ id: "account-1" }), { status: 200 });
  };
  assert.equal((await api.createAccount("Gym")).id, "account-1");
});

test("LeiBox spring settles exactly rather than scheduling a permanent animation", () => {
  const state = { p: 0, v: 0, target: 410 };
  let moving = true;
  for (let frame = 0; frame < 600 && moving; frame += 1) {
    moving = false;
    for (let i = 0; i < 3; i += 1) moving = stepSpring(state, 1 / 180) || moving;
  }
  assert.equal(moving, false);
  assert.equal(state.p, 410);
  assert.equal(state.v, 0);
});

test("resize keeps legal widths while resisting the excess beyond an edge", () => {
  assert.equal(clamp(560, 300, 520), 520);
  assert.equal(resistEdge(400, 300, 520), 400);
  assert.ok(resistEdge(800, 300, 520) > 520 && resistEdge(800, 300, 520) < 535);
  assert.ok(resistEdge(100, 300, 520) < 300 && resistEdge(100, 300, 520) > 285);
});

test("status capsule follows a finite trajectory and lands in the selected control", () => {
  const frames = pillFlightFrames({ left: 100, top: 420, width: 29, height: 14 }, { left: 150, top: 260, width: 32, height: 22 });
  assert.equal(frames.length, 23);
  assert.match(frames[0].transform, /translate3d\(100px,420px,0\)/);
  assert.match(frames.at(-1).transform, /translate3d\(151.5px,264px,0\)/);
  assert.ok(frames.every(frame => !/NaN|Infinity/.test(frame.transform)));
  assert.match(frames.at(-1).transform, /scale\(1\)/);
});

test("late job responses cannot overwrite a newer terminal result", () => {
  const completed = { id: "one", status: "completed", updated_at: "2026-11-11T10:00:02Z" };
  const old = { id: "one", status: "running", updated_at: "2026-11-11T10:00:01Z" };
  assert.equal(mergeJobState(completed, old).status, "completed");
});

test("event replay is ordered and idempotent", () => {
  const events = reconcileEvents([{ id: 2, message: "old" }, { id: 1 }, { id: 2, message: "new" }]);
  assert.deepEqual(events.map(event => event.id), [1, 2]);
  assert.equal(events[1].message, "new");
});

test("controls only expose server-authorized operations", () => {
  assert.equal(canJobAction({ status: "incomplete", actions: ["retry", "open_assignment"] }, "continue"), false);
  assert.equal(canJobAction({ actions: ["retry"] }, "retry"), true);
});

test("queue and manual pause cannot pretend the browser is allocated", () => {
  const queued = buildWorkflowStages({ status: "queued", phase: "queued", waiting_reason: "等待前一个任务" }, []);
  assert.equal(queued[0].state, "active");
  assert.equal(queued[1].state, "pending");
  assert.equal(queued[4].state, "pending");
  const paused = buildWorkflowStages({ status: "paused", phase: "queued", paused_from_status: "queued" }, []);
  assert.equal(paused[0].state, "paused");
});

test("a partial interrupted run is not shown as all answers completed", () => {
  const stages = buildWorkflowStages({ status: "interrupted", started_at: "now", phase: "interrupted" }, [{ kind: "session_ready" }, { kind: "agent_ready" }, { kind: "quiz_extracted" }, { kind: "quiz_unit_filled" }], { completedCount: 1, unitLabel: "作答单元" });
  assert.equal(stages[4].state, "error");
  assert.equal(stages[5].state, "error");
});

test("review and actual submitted completion have different final stages", () => {
  assert.equal(buildWorkflowStages({ status: "needs_intervention", submission_status: "filled_pending_review" }, [{ kind: "quiz_filled" }])[5].state, "attention");
  assert.equal(buildWorkflowStages({ status: "completed", submission_status: "submitted_verified" }, [])[5].state, "done");
});

test("retry always sends the currently confirmed submission mode", async () => {
  globalThis.fetch = async (url, options) => {
    assert.match(url, /\/retry$/);
    assert.deepEqual(JSON.parse(options.body), { auto_submit: false });
    return new Response(JSON.stringify({ id: "retry" }));
  };
  await api.retryJob("one");
});
