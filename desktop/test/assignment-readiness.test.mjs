import { test } from "node:test";
import assert from "node:assert/strict";
import { assignmentReadiness, assignmentProcessingReason, directlyAnswerable, requiresMaterials } from "../src/features/assignments/lib/readiness.js";

test("a platform submit button alone never claims that answers are ready", () => {
  assert.equal(assignmentReadiness({ submittable: true }).label, "待检查");
  assert.equal(assignmentReadiness({ submittable: true, assessment: { answer_state: "empty" } }).label, "待作答");
  assert.equal(assignmentReadiness({ assessment: { answer_state: "filled" } }).label, "已填写，待核验");
});

test("material-based experiments stay manual even though an editor and submit button exist", () => {
  const assignment = { submittable: true, assessment: { answer_state: "empty", execution_state: "manual_required", requirements: ["实验或实践报告", "项目源码", "演示视频"] } };
  assert.ok(requiresMaterials(assignment));
  assert.equal(assignmentReadiness(assignment).label, "需材料");
  assert.ok(assignmentReadiness(assignment).detail.includes("项目源码"));
});

test("browser-completable mixed assignments share one processing decision", () => {
  assert.equal(directlyAnswerable({ assessment: { policy_version: 3, execution_state: "direct_fill", automation_supported: true } }), true);
  assert.equal(directlyAnswerable({ assessment: { policy_version: 2, execution_state: "direct_fill", automation_supported: true } }), false);
  assert.equal(directlyAnswerable({ assessment: { execution_state: "direct_fill", automation_supported: true } }), false);
  assert.equal(directlyAnswerable({ assessment: { execution_state: "direct_fill", automation_supported: false } }), false);
  const unsupported = {
    assessment: {
      answer_state: "empty",
      execution_state: "manual_required",
      requirements: [],
      reason: "通知式作业，没有具体作答任务",
    },
  };
  assert.ok(requiresMaterials(unsupported));
  assert.equal(assignmentReadiness(unsupported).label, "需人工处理");
  assert.ok(assignmentProcessingReason(unsupported).includes("通知"));
  assert.equal(directlyAnswerable({submittable: false, assessment: { execution_state: "direct_fill", automation_supported: true }}), false);
  assert.equal(directlyAnswerable({assessment: {execution_state: "manual_required", automation_supported: true}}), false);
  assert.equal(directlyAnswerable({assessment: {execution_state: "direct_fill"}}), false);
});
