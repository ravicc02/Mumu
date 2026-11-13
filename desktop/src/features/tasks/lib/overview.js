import { conciseMessage } from "./presentation.js";

export function buildTaskOverview(assignments, learningRuns) {
  const records = [
    ...assignments.map(job => ({ ...job, task_kind: "assignment", task_key: `assignment:${job.account_id}:${job.assignment_id}` })),
    ...learningRuns.map(run => ({
      ...run, task_kind: "learning", task_key: `learning:${run.account_id}:${run.course_id}`,
      raw_status: run.status, status: run.status === "awaiting_user" ? "needs_intervention" : run.status,
      assignment_title: run.course_name, course_name: "网课",
    })),
  ].sort((left, right) => Date.parse(right.created_at) - Date.parse(left.created_at));
  const latest = new Map();
  for (const record of records) if (!latest.has(record.task_key)) latest.set(record.task_key, record);
  return [...latest.values()].sort((left, right) => Date.parse(right.updated_at || right.created_at) - Date.parse(left.updated_at || left.created_at));
}

export function overviewResult(task, submissionLabel) {
  if (task.platform === "educoder" && task.task_kind !== "learning") {
    if (task.status === "completed") return "关卡与课堂结果已核验";
    if (task.status === "needs_intervention") return "代码已保存，待审核评测";
    return conciseMessage(task.error) || '已通过 ' + (task.completed_units || 0) + '/' + (task.total_units || '?') + ' 关';
  }
  if (task.task_kind !== "learning") return submissionLabel(task.submission_status);
  if (task.status === "completed") return "平台进度已核验";
  if (task.status === "needs_intervention") return conciseMessage(task.error) || "等待人工处理";
  if (["incomplete", "interrupted"].includes(task.status)) return conciseMessage(task.error) || "执行异常";
  if (task.status === "stopped") return "用户已停止";
  return task.current_unit || task.current_chapter || (task.status === "queued" ? "等待浏览器" : "正在读取课程");
}
