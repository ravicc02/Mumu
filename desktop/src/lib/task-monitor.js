import { conciseMessage } from "./task-presentation.js";

const BUSY = new Set(["queued", "running", "paused", "stopping"]);
const ERRORS = new Set(["failed", "interrupted", "incomplete"]);
const REVIEW = new Set(["needs_intervention", "awaiting_user"]);
const timestamp = value => Date.parse(value || "") || 0;
export const monitorTaskKey = task => task.task_key || [task.task_kind || "assignment", task.account_id, task.assignment_id || task.course_id || task.id].join(":");

export function newMonitorState() {
  return { version: 1, initialized: false, seen: {}, round: null };
}

export function restoreMonitorState(value) {
  if (value?.version !== 1 || !value.seen || typeof value.seen !== "object" || Array.isArray(value.seen)) return newMonitorState();
  const seen = Object.fromEntries(Object.entries(value.seen).filter(([key, item]) => typeof key === "string" && item && typeof item.id === "string").slice(-512));
  const round = value.round && Array.isArray(value.round.entries) && value.round.entries.length <= 256
    && value.round.entries.every(item => item && typeof item.id === "string" && typeof item.key === "string")
    ? value.round : null;
  return { version: 1, initialized: true, seen, round };
}

function member(task) {
  return { key: monitorTaskKey(task), id: task.id, task_kind: task.task_kind || "assignment", status: task.status,
    submission_status: task.submission_status, account_id: task.account_id, assignment_id: task.assignment_id,
    course_id: task.course_id, assignment_title: task.assignment_title || task.course_name || "任务",
    account_name: task.account_name || "", course_name: task.course_name || "", platform: task.platform,
    error: conciseMessage(task.error) || "", progress: task.progress || 0, updated_at: task.updated_at, created_at: task.created_at };
}

export function reconcileMonitor(previous, tasks, { now = Date.now(), ready = true, removedIds = [] } = {}) {
  if (!ready || !Array.isArray(tasks)) return previous;
  const state = JSON.parse(JSON.stringify(previous));
  const removed = new Set(removedIds);
  if (state.round) state.round.entries = state.round.entries.filter(item => !removed.has(item.id));
  for (const [key, item] of Object.entries(state.seen)) if (removed.has(item.id)) delete state.seen[key];
  const records = new Map();
  for (const task of tasks) {
    if (removed.has(task.id)) continue;
    const key = monitorTaskKey(task), older = records.get(key);
    if (!older || timestamp(task.created_at) > timestamp(older.created_at)
      || (task.id === older.id && timestamp(task.updated_at) >= timestamp(older.updated_at))) records.set(key, task);
  }
  const unseen = [];
  for (const [key, task] of records) {
    const seen = state.seen[key];
    if (seen && seen.id !== task.id && timestamp(task.created_at) <= timestamp(seen.created_at)) continue;
    if (state.initialized && (!seen || seen.id !== task.id)) unseen.push(task);
    state.seen[key] = { id: task.id, created_at: task.created_at };
  }
  const alreadySettled = state.round?.entries.every(item => !BUSY.has(item.status) && !REVIEW.has(item.status)
    && !(item.status === "completed" && item.task_kind !== "learning" && item.submission_status !== "submitted_verified"));
  if (!state.initialized) {
    const active = [...records.values()].filter(task => BUSY.has(task.status));
    if (active.length) state.round = { id: now, started_at: now, entries: active.map(member) };
  } else if (unseen.length) {
    const sameWork = state.round && unseen.some(task => state.round.entries.some(item => item.key === monitorTaskKey(task)));
    if (!state.round || (alreadySettled && !sameWork)) state.round = { id: now, started_at: now, entries: [] };
    const keys = new Set(state.round.entries.map(item => item.key));
    for (const task of unseen) {
      if (!keys.has(monitorTaskKey(task))) { state.round.entries.push(member(task)); keys.add(monitorTaskKey(task)); }
    }
  }
  if (state.round) {
    state.round.entries = state.round.entries.map(item => {
      const task = records.get(item.key);
      if (!task || timestamp(task.created_at) < timestamp(item.created_at)
        || (task.id === item.id && timestamp(task.updated_at) < timestamp(item.updated_at))) return item;
      return member(task);
    });
  }
  state.seen = Object.fromEntries(Object.entries(state.seen).slice(-512));
  state.initialized = true;
  return state;
}

export function monitorSummary(state, { online = true, ready = true } = {}) {
  const entries = state.round?.entries || [];
  const completed = entries.filter(item => item.status === "completed"
    && (item.task_kind === "learning" || item.submission_status === "submitted_verified")).length;
  const errors = entries.filter(item => ERRORS.has(item.status)).length;
  const review = entries.filter(item => REVIEW.has(item.status) || (item.status === "completed" && item.task_kind !== "learning" && item.submission_status !== "submitted_verified")).length;
  const running = entries.filter(item => ["running", "stopping"].includes(item.status)).length;
  const queued = entries.filter(item => item.status === "queued").length;
  const paused = entries.filter(item => item.status === "paused").length;
  const stopped = entries.filter(item => item.status === "stopped").length;
  const total = entries.length;
  const ratio = total ? completed / total : 0;
  let mood = "idle", label = "待机", detail = "暂无执行中的任务";
  if (!ready) { label = "同步中"; detail = "正在读取任务状态"; }
  else if (!online) { label = "连接中断"; detail = "等待本地服务恢复，保留上次进度"; }
  else if (total && completed === total) { mood = "happy"; label = "完工"; detail = "本轮任务全部完成"; }
  else if (errors >= 2 || (errors > 0 && !running && !queued && !paused)) {
    mood = "angry"; label = "执行受阻"; detail = errors + " 个任务未完成，需要检查原因";
  } else if (running || queued) {
    mood = "working"; label = "处理中"; detail = running ? running + " 个执行中" + (queued ? " · " + queued + " 个排队" : "") : queued + " 个等待执行";
  } else if (review) { mood = "working"; label = "等你审核"; detail = review + " 个任务待人工处理"; }
  else if (paused) { label = "已暂停"; detail = paused + " 个任务已人工暂停"; }
  else if (stopped) { label = "已停止"; detail = stopped + " 个任务已停止"; }
  return { mood, label, detail, total, completed, errors, review, running, queued, paused, stopped,
    ratio, water: total ? ratio : .09, percent: Math.round(ratio * 100), entries,
    busy: Boolean(running || queued || paused), attention: errors + review,
    roundId: state.round?.id || null };
}
