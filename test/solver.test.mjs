import test from "node:test";
import assert from "node:assert/strict";
import {
  extractJsonObject,
  normalizeAnswer,
  normalizeQuestion,
  solveQuestion
} from "../server/solver.mjs";
import { normalizeSyncPayload } from "../server/assignment-store.mjs";

const question = normalizeQuestion({
  type: "single_choice",
  stem: "Manifest V3 的后台脚本叫什么？",
  options: [
    { index: 0, text: "Service Worker" },
    { index: 1, text: "Content Script" },
    { index: 2, text: "Popup" }
  ]
});

test("extractJsonObject reads fenced JSON", () => {
  const value = extractJsonObject("```json\n{\"type\":\"single_choice\",\"selectedIndices\":[0]}\n```");
  assert.equal(value.selectedIndices[0], 0);
});

test("normalizeAnswer drops out-of-range selections", () => {
  const answer = normalizeAnswer({
    type: "multiple_choice",
    selectedIndices: [0, 0, 9, 2],
    confidence: 1.7
  }, { ...question, type: "multiple_choice" });

  assert.deepEqual(answer.selectedIndices, [0, 2]);
  assert.equal(answer.confidence, 1);
});

test("mock solver returns a usable demo answer", async () => {
  const answer = await solveQuestion(question, { mockMode: true });
  assert.equal(answer.mode, "mock");
  assert.deepEqual(answer.selectedIndices, [0]);
  assert.ok(answer.explanation.includes("演示模式"));
});

test("live solver forwards reasoning effort to OpenAI-compatible endpoints", async () => {
  let requestBody;
  const fetchImpl = async (_url, options) => {
    requestBody = JSON.parse(options.body);
    return new Response(JSON.stringify({
      choices: [{
        message: {
          content: JSON.stringify({
            type: "single_choice",
            selectedIndices: [0],
            textAnswer: "",
            confidence: 0.9,
            explanation: "测试"
          })
        }
      }]
    }), {
      status: 200,
      headers: { "Content-Type": "application/json" }
    });
  };

  const answer = await solveQuestion(question, {
    mockMode: false,
    baseUrl: "https://example.test/v1",
    apiKey: "test-key",
    model: "test-model",
    reasoningEffort: "high",
    jsonMode: false,
    timeoutMs: 1000
  }, fetchImpl);

  assert.equal(requestBody.reasoning_effort, "high");
  assert.equal(requestBody.model, "test-model");
  assert.equal(answer.reasoningEffort, "high");
});

test("assignment sync deduplicates by work and prefers unfinished status", () => {
  const snapshot = normalizeSyncPayload({
    academicYear: 2026,
    coursesScanned: 1,
    assignments: [
      {
        courseName: "测试课程",
        courseId: "1",
        classId: "2",
        workId: "3",
        title: "作业 A",
        status: "待互评"
      },
      {
        courseName: "测试课程",
        courseId: "1",
        classId: "2",
        workId: "3",
        title: "作业 A",
        status: "未交"
      }
    ]
  });

  assert.equal(snapshot.assignments.length, 1);
  assert.equal(snapshot.assignments[0].status, "unfinished");
});

test("assignment sync preserves submission availability", () => {
  const snapshot = normalizeSyncPayload({
    academicYear: 2026,
    assignments: [{
      courseName: "测试课程",
      courseId: "1",
      classId: "2",
      workId: "3",
      title: "作业 A",
      status: "未交",
      submittable: true,
      deadlineText: "11-01 至 11-30"
    }]
  });

  assert.equal(snapshot.assignments[0].submittable, true);
  assert.equal(snapshot.assignments[0].deadlineText, "11-01 至 11-30");
});
