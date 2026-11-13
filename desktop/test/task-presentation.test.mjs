import { test } from "node:test";
import assert from "node:assert/strict";
import { conciseMessage, presentEvent, taskIssue } from "../src/features/tasks/lib/presentation.js";
import { artifactUrl } from "../src/app/lib/backend-url.js";

test("successful code progress with timeout terminology is not rewritten as a connection failure", () => {
  const message = "当前关卡代码已保存并完整回读：将注解改为 @Test(timeout = 1000)，实现超时测试。";
  assert.equal(presentEvent({kind:"draft_saved",message}).message,message);
  assert.equal(presentEvent({kind:"educoder_read",message:"正在分析关卡：超时测试"}).message,"正在分析关卡：超时测试");
  assert.match(presentEvent({kind:"interrupted",message:"模型请求 timeout"}).message,/连接超时/);
});

test("result artifacts use the configured backend and encode their identifier", () => {
  assert.equal(artifactUrl("capture", "http://127.0.0.1:8879/"), "http://127.0.0.1:8879/api/artifacts/capture");
  assert.equal(artifactUrl("one/two", "http://127.0.0.1:8879"), "http://127.0.0.1:8879/api/artifacts/one%2Ftwo");
});

test("verified completion takes precedence over an old save-only summary", () => {
  const oldSummary = "当前关卡代码已保存，尚未评测；已通过 1/4 关。";
  assert.equal(taskIssue({platform:"educoder",status:"completed",submission_status:"submitted_verified"},oldSummary),"实验全部关卡已通过，课堂结果已核验。");
  assert.equal(taskIssue({platform:"chaoxing",status:"completed",submission_status:"submitted_verified"},oldSummary),"作业已完成，提交已验证。");
  assert.equal(taskIssue({platform:"educoder",status:"incomplete",submission_status:"not_submitted"},oldSummary),oldSummary);
});

test("verification errors are actionable and raw technical evidence stays unchanged", () => {
  const raw = "页面填写与核验失败：以下作答单元写入后未通过核验：q406430345:r1 最后页面：https://mooc1.chaoxing.com/work?enc=secret";
  const event = { id: 10, kind: "incomplete", message: raw, data: { failed_unit: "q406430345:r1" } };
  assert.equal(presentEvent(event).message, "部分题目未通过核验，请在浏览器检查后重试。");
  assert.equal(event.message, raw);
  assert.equal(presentEvent(event).data.failed_unit, "q406430345:r1");
  assert.equal(taskIssue({ status: "incomplete", error: raw }), presentEvent(event).message);
});

test("common failures have concise human explanations without signed links", () => {
  assert.match(conciseMessage("通知发送失败：微信发送失败: -2"), /微信/);
  assert.match(conciseMessage("模型请求 timeout at https://provider.test?key=secret"), /超时/);
  assert.match(conciseMessage("本地后端重新启动，原浏览器会话结束"), /重启/);
  assert.match(taskIssue({ status: "needs_intervention" }), /人工审核/);
  assert.ok(conciseMessage("x".repeat(500)).length <= 86);
  assert.ok(!conciseMessage("控件异常 https://example.test/work?enc=secret").includes("secret"));
});
