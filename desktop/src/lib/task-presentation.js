// Presentation only. Original event/error data remains available in diagnostics.
export function conciseMessage(value, { kind = "", limit = 86 } = {}) {
  const raw = String(value || "").trim();
  if (!raw) return "";
  const failureContext = !kind || ["error", "failed", "interrupted", "incomplete", "warning", "notification", "notification_error", "needs_intervention"].includes(kind);
  if (failureContext) {
    if (/通知.*失败|推送.*失败/.test(raw)) return "消息推送失败，请检查微信连接。";
    if (/写入后未通过核验|填写与核验失败|页面.*核验失败/.test(raw)) return "部分题目未通过核验，请在浏览器检查后重试。";
    if (/登录.*失效|登录页面|Cookie.*失效|cookie.*expired/i.test(raw)) return "登录状态已失效，请刷新账号授权后重试。";
    if (/未读取到.*章节目录/.test(raw)) return "未能读取课程目录，请在浏览器确认课程已加载后重试。";
    if (/应用重启|后端.*重新启动|浏览器会话结束/.test(raw)) return "本地服务重启，浏览器会话已结束，请重试任务。";
    if (/暂存|暂时保存/.test(raw) && /失败|未确认|找不到/.test(raw)) return "作答进度未能保存，请在浏览器检查后手动暂存。";
    if (/rate.?limit|\b429\b/i.test(raw)) return "模型服务请求过多，请稍后重试。";
    if (/\b(?:401|403)\b|api.?key|unauthorized/i.test(raw)) return "服务授权失败，请检查模型或平台授权配置。";
    if (/timeout|timed out|超时/i.test(raw)) return "连接超时，请检查网络后重试。";
    if (/\b(?:502|503|504)\b|connection.*(error|reset|refused)|网络.*(断开|失败)/i.test(raw)) return "上游服务暂不可用，请检查网络后重试。";
  }
  if (/作答进度已暂时保存/.test(raw) && /人工/.test(raw)) return "已填写并暂存，等待人工审核与提交。";
  if (/本地 OCS 课程模块/.test(raw)) return "已连接账号浏览器，内置网课引擎已就绪";
  const text = raw.replace(/https?:\/\/[^\s<>，。；]+/gi, "")
    .replace(/\bq\d+:[a-z]\d+\b/gi, "")
    .replace(/\b(?:Traceback|Stack trace)[\s\S]*/gi, "")
    .replace(/\s+/g, " ").trim();
  if (!text) return ["error", "failed", "interrupted", "incomplete"].includes(kind) ? "执行异常，请将设置中日志目录下的任务日志发给维护者。" : "页面状态已更新";
  return text.length > limit ? text.slice(0, limit - 1).replace(/[，；、：:,;\s]+$/, "") + "…" : text;
}

export function taskIssue(job, fallback = "") {
  if (!job) return "";
  if (job.status === "completed" && job.submission_status === "submitted_verified") {
    return job.platform === "educoder" ? "实验全部关卡已通过，课堂结果已核验。" : "作业已完成，提交已验证。";
  }
  if (job.status === "needs_intervention") return "已填写，等待人工审核与提交。";
  if (job.error) return conciseMessage(job.error, { kind: job.status });
  return conciseMessage(fallback, { kind: job.status });
}

export function presentEvent(event) {
  return { ...event, message: conciseMessage(event?.message, { kind: event?.kind }) };
}
