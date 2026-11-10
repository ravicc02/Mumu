import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const serverDirectory = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(serverDirectory, "..");
const dataDirectory = path.join(projectRoot, "data");
const dataFile = path.join(dataDirectory, "assignments.json");

const STATUS_PRIORITY = new Map([
  ["unfinished", 0],
  ["pending_review", 1],
  ["completed", 2],
  ["unknown", 3]
]);

let snapshot = createEmptySnapshot();

function createEmptySnapshot() {
  return {
    version: 1,
    source: "chaoxing",
    academicYear: new Date().getFullYear(),
    syncedAt: "",
    coursesScanned: 0,
    assignments: []
  };
}

function cleanString(value, maxLength = 2000) {
  return String(value ?? "").trim().slice(0, maxLength);
}

function normalizeStatus(value) {
  const status = cleanString(value, 40);
  if (["未交", "未完成"].includes(status)) {
    return "unfinished";
  }
  if (status === "待互评") {
    return "pending_review";
  }
  if (status === "已完成") {
    return "completed";
  }
  return "unknown";
}

function normalizeAssignment(value, academicYear) {
  return {
    academicYear,
    courseName: cleanString(value.courseName || value.course, 500),
    courseId: cleanString(value.courseId, 80),
    classId: cleanString(value.classId, 80),
    startDate: cleanString(value.startDate, 20),
    title: cleanString(value.title, 1000),
    statusRaw: cleanString(value.statusRaw || value.status, 40),
    status: normalizeStatus(value.statusRaw || value.status),
    submittable: value.submittable === true,
    deadlineText: cleanString(value.deadlineText, 300),
    finalTaskUrl: cleanString(value.finalTaskUrl, 4000),
    timeText: cleanString(value.timeText, 200),
    taskUrl: cleanString(value.taskUrl, 4000),
    workId: cleanString(value.workId, 80)
  };
}

function assignmentKey(assignment) {
  return [
    assignment.academicYear,
    assignment.courseId,
    assignment.classId,
    assignment.workId || assignment.taskUrl || assignment.title
  ].join("|");
}

export function normalizeSyncPayload(payload) {
  if (!payload || typeof payload !== "object") {
    throw new Error("同步数据不能为空");
  }
  const academicYear = Number(payload.academicYear);
  if (!Number.isInteger(academicYear) || academicYear < 2000 || academicYear > 2100) {
    throw new Error("academicYear 无效");
  }

  const assignments = Array.isArray(payload.assignments)
    ? payload.assignments
        .map((assignment) => normalizeAssignment(assignment, academicYear))
        .filter((assignment) => assignment.title)
    : [];
  const deduplicated = new Map();
  for (const assignment of assignments) {
    const key = assignmentKey(assignment);
    const current = deduplicated.get(key);
    if (
      !current
      || (STATUS_PRIORITY.get(assignment.status) ?? 99)
        < (STATUS_PRIORITY.get(current.status) ?? 99)
    ) {
      deduplicated.set(key, assignment);
    }
  }

  return {
    version: 1,
    source: cleanString(payload.source || "chaoxing", 80),
    academicYear,
    syncedAt: cleanString(payload.syncedAt, 80) || new Date().toISOString(),
    coursesScanned: Number.isInteger(payload.coursesScanned)
      ? Math.max(0, payload.coursesScanned)
      : new Set(assignments.map((item) => `${item.courseId}_${item.classId}`)).size,
    assignments: [...deduplicated.values()]
  };
}

export async function loadAssignmentStore() {
  try {
    const content = await readFile(dataFile, "utf8");
    snapshot = normalizeSyncPayload(JSON.parse(content));
  } catch (error) {
    if (error.code !== "ENOENT") {
      console.warn("Unable to load assignment store", error);
    }
    snapshot = createEmptySnapshot();
  }
  return snapshot;
}

export async function saveAssignmentSnapshot(payload) {
  snapshot = normalizeSyncPayload(payload);
  await mkdir(dataDirectory, { recursive: true });
  const temporaryFile = `${dataFile}.tmp`;
  await writeFile(temporaryFile, `${JSON.stringify(snapshot, null, 2)}\n`, "utf8");
  await rename(temporaryFile, dataFile);
  return snapshot;
}

export function getAssignmentSnapshot() {
  return snapshot;
}

export function queryAssignments({
  academicYear,
  status,
  courseId,
  submittable
} = {}) {
  return snapshot.assignments.filter((assignment) => {
    if (academicYear && assignment.academicYear !== Number(academicYear)) {
      return false;
    }
    if (status && assignment.status !== status) {
      return false;
    }
    if (courseId && assignment.courseId !== String(courseId)) {
      return false;
    }
    if (submittable === true && !assignment.submittable) {
      return false;
    }
    if (submittable === false && assignment.submittable) {
      return false;
    }
    return true;
  });
}

export function getAssignmentSummary({
  academicYear,
  status,
  submittable
} = {}) {
  const assignments = queryAssignments({ academicYear, status, submittable });
  const groups = new Map();
  for (const assignment of assignments) {
    const key = `${assignment.courseId}_${assignment.classId}`;
    if (!groups.has(key)) {
      groups.set(key, {
        courseName: assignment.courseName,
        courseId: assignment.courseId,
        classId: assignment.classId,
        startDate: assignment.startDate,
        assignments: []
      });
    }
    groups.get(key).assignments.push(assignment);
  }
  const courses = [...groups.values()].map((group) => ({
    ...group,
    unfinishedCount: group.assignments.filter((item) => item.status === "unfinished").length
  }));
  return {
    ok: true,
    source: snapshot.source,
    academicYear: snapshot.academicYear,
    syncedAt: snapshot.syncedAt,
    coursesScanned: snapshot.coursesScanned,
    assignmentCount: assignments.length,
    unfinishedCount: assignments.filter((item) => item.status === "unfinished").length,
    courses
  };
}
