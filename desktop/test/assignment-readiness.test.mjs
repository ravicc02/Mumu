import { test } from "node:test";
import assert from "node:assert/strict";
import { assignmentReadiness, requiresMaterials } from "../src/features/assignments/lib/readiness.js";

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
