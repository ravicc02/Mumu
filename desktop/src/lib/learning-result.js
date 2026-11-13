import { conciseMessage } from "./task-presentation.js";

export const learningStatusLabel = status => ({ queued: "排队中", running: "运行中", paused: "已暂停", awaiting_user: "待介入", stopping: "停止中", stopped: "已停止", interrupted: "中断", incomplete: "未完成", completed: "已完成" })[status] || "未知状态";

export function learningScreenshotSections(artifacts, source) {
  const images = artifacts.filter(item => item.id && ["screenshot", "final_screenshot", "last_screenshot"].includes(item.kind));
  return images.map((item, index) => ({ id: item.id, src: source(item.id), index: index + 1, count: images.length }));
}

export function learningResultSummary(run, events = []) {
  const target = new Set(run?.target || []), completed = new Set(run?.completed || []);
  const verified = run?.status === "completed" && run?.phase === "verified";
  return {
    verified,
    title: verified ? "全部章节已通过平台复验" : conciseMessage(run?.error) || learningStatusLabel(run?.status),
    total: target.size,
    completed: [...completed].filter(id => target.has(id)).length,
    checkpoints: events.filter(event => ["chapter_verified", "chapter_skipped", "completed"].includes(event.kind)),
  };
}

export async function loadLearningResult(api, identifier) {
  const [run, events, artifacts] = await Promise.all([api.learningRun(identifier), api.learningEvents(identifier), api.learningArtifacts(identifier)]);
  if (run.id !== identifier) throw new Error("返回的网课记录与当前任务不一致，请重新加载。");
  return { run, events, sections: learningScreenshotSections(artifacts, api.learningArtifactUrl), summary: learningResultSummary(run, events) };
}
