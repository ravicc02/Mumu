export function mergeJobState(previous, incoming) {
  if (!previous) return incoming;
  const before = Date.parse(previous.updated_at || previous.created_at || "");
  const after = Date.parse(incoming.updated_at || incoming.created_at || "");
  return Number.isFinite(before) && Number.isFinite(after) && after < before
    ? previous : { ...previous, ...incoming };
}

export function canJobAction(job, action) {
  return Boolean(job?.actions?.includes(action));
}

export function reconcileEvents(events) {
  return [...new Map(events.map(event => [event.id, event])).values()]
    .sort((left, right) => Number(left.id) - Number(right.id));
}

export function buildWorkflowStages(job, events, { completedCount = 0, unitLabel = "动作步骤" } = {}) {
  if (!job) return [];
  const kinds = new Set(events.map(event => event.kind));
  const running = ["running", "stopping"].includes(job.status);
  const paused = job.status === "paused";
  const queued = job.status === "queued" || (paused && job.paused_from_status === "queued");
  const review = job.status === "needs_intervention";
  const complete = job.status === "completed" && job.submission_status === "submitted_verified";
  const failed = ["incomplete", "interrupted", "failed"].includes(job.status);
  const stopped = job.status === "stopped";
  const phase = job.phase;
  const browserDone = kinds.has("session_ready") || kinds.has("browser");
  const agentDone = kinds.has("agent_ready") || kinds.has("quiz_extracted");
  const analyzed = kinds.has("quiz_extracted") || kinds.has("step") || kinds.has("quiz_unit_filled");
  const experiment = job.platform === "educoder";
  const experimentDetail = phase === "repairing" ? "根据真实编译或测试反馈修复代码"
    : ["testing", "evaluating"].includes(phase) ? "正在等待平台评测回执"
    : phase === "planning" ? "正在分析要求并生成受限修改"
    : '已通过 ' + completedCount + '/' + job.total_units + ' 关';
  const filled = experiment ? complete || (completedCount >= job.total_units && job.total_units > 0) : kinds.has("quiz_filled") || review || complete;
  const current = (active, failure = false) => paused && !queued ? "paused"
    : running && active ? "active" : failed && failure ? "error" : stopped && active ? "stopped" : "pending";
  return [
    { id: "queued", label: "任务排队", detail: job.waiting_reason || (queued ? "等待当前账号执行器" : job.started_at ? "执行器已分配" : "任务已创建"),
      state: job.started_at || browserDone ? "done" : paused ? "paused" : queued ? "active" : failed ? "error" : stopped ? "stopped" : "pending" },
    { id: "browser", label: "登录与浏览器", detail: browserDone ? "登录态已验证，浏览器已连接" : "等待浏览器就绪",
      state: browserDone ? "done" : current(phase === "browser_starting", Boolean(job.started_at)) },
    { id: "agent", label: "Agent 初始化", detail: agentDone ? "模型与页面控制已就绪" : "等待模型和页面控制",
      state: agentDone ? "done" : current(phase === "agent_starting", browserDone) },
    { id: "analyze", label: "页面分析", detail: analyzed ? "已读取题目与作答控件" : "等待读取题目",
      state: analyzed ? "done" : current(["extracting", "agent_running"].includes(phase), agentDone) },
    { id: "execute", label: experiment ? "关卡执行" : "执行步骤", detail: experiment ? experimentDetail : `已处理 ${completedCount} 个${unitLabel}`,
      state: filled ? "done" : experiment && review ? "attention" : failed && analyzed ? "error" : current(["filling", "agent_running", "evaluating", "planning", "testing", "repairing"].includes(phase)) },
    { id: "result", label: "结果确认", detail: complete ? "作业完成，提交已确认" : review ? (kinds.has("draft_saved") ? "已暂存，等待审核与提交" : "已填写，等待审核与提交") : stopped ? "用户已停止" : failed ? "查看具体原因后处理或重试" : phase === "saving" ? "正在暂时保存作答进度" : "等待执行与提交核验",
      state: complete ? "done" : review ? "attention" : failed ? "error" : stopped ? "stopped" : current(["verifying", "saving"].includes(phase)) }
  ].map(stage => stage.state === "paused" ? { ...stage, detail: "人工暂停，等待恢复" } : stage);
}
