export const TOWN_DISTRICTS = [
  { id: "active", label: "进行中", color: "#4389e5", floor: "#dce9f5", x: -7.2, z: -4.15 },
  { id: "review", label: "待介入", color: "#dca52d", floor: "#f2ebd6", x: 0, z: -4.15 },
  { id: "completed", label: "已完成", color: "#38aa87", floor: "#d7ebe3", x: 7.2, z: -4.15 },
  { id: "interrupted", label: "中断", color: "#e36560", floor: "#f4dfdd", x: -7.2, z: 4.15 },
  { id: "incomplete", label: "未完成", color: "#bb658d", floor: "#eedfe8", x: 0, z: 4.15 },
  { id: "paused", label: "已暂停", color: "#83929e", floor: "#e2e8eb", x: 7.2, z: 4.15 },
];

export function townDistrict(task) {
  const status = task.status;
  if (["queued", "running", "stopping"].includes(status)) return "active";
  if (["needs_intervention", "awaiting_user"].includes(status)) return "review";
  if (["interrupted", "failed"].includes(status)) return "interrupted";
  if (status === "incomplete") return "incomplete";
  if (status === "completed") return "completed";
  return "paused";
}

export function townTaskKey(task) {
  return task.task_key || `${task.task_kind || "assignment"}:${task.account_id}:${task.assignment_id || task.course_id || task.id}`;
}

export function stableSeed(value) {
  let hash = 2166136261;
  for (const character of String(value)) hash = Math.imul(hash ^ character.charCodeAt(0), 16777619);
  return hash >>> 0;
}

export const BOT_SHAPES = ["orb", "soft-cube", "capsule", "pebble", "bean", "pear", "droplet", "pillow", "triangle", "diamond", "squash", "clover"];

export function botShape(key) { return BOT_SHAPES[stableSeed(key) % BOT_SHAPES.length]; }


export function townTaskActions(task) {
  return {
    detailsLabel: task.task_kind === "learning" && ["completed", "incomplete", "interrupted", "stopped"].includes(task.status) ? "查看结果" : "查看任务",
    deletable: Boolean(task.actions?.includes("delete")),
  };
}
