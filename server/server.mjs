import { createServer } from "node:http";
import { readFile, stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  normalizeQuestion,
  solveQuestion
} from "./solver.mjs";
import {
  getAssignmentSnapshot,
  getAssignmentSummary,
  loadAssignmentStore,
  queryAssignments,
  saveAssignmentSnapshot
} from "./assignment-store.mjs";

const serverDirectory = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(serverDirectory, "..");
const demoRoot = path.join(projectRoot, "demo");
const envFilePath = path.join(projectRoot, ".env");

async function loadEnvironmentFile() {
  let content;
  try {
    content = await readFile(envFilePath, "utf8");
  } catch (error) {
    if (error.code === "ENOENT") {
      return;
    }
    throw error;
  }

  for (const line of content.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) {
      continue;
    }
    const separator = trimmed.indexOf("=");
    if (separator === -1) {
      continue;
    }
    const key = trimmed.slice(0, separator).trim();
    let value = trimmed.slice(separator + 1).trim();
    if (
      (value.startsWith("\"") && value.endsWith("\""))
      || (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    if (key && process.env[key] === undefined) {
      process.env[key] = value;
    }
  }
}

await loadEnvironmentFile();
await loadAssignmentStore();

const apiKey = process.env.OPENAI_API_KEY?.trim() || "";
const explicitMockMode = process.env.AI_MOCK === "true";
const config = {
  baseUrl: process.env.OPENAI_BASE_URL?.trim() || "https://api.openai.com/v1",
  apiKey,
  model: process.env.OPENAI_MODEL?.trim() || "gpt-4.1-mini",
  reasoningEffort: process.env.AI_REASONING_EFFORT?.trim() || "",
  jsonMode: process.env.OPENAI_JSON_MODE === "true",
  mockMode: explicitMockMode || !apiKey,
  timeoutMs: Number(process.env.AI_TIMEOUT_MS) || 60000,
  host: process.env.HOST?.trim() || "127.0.0.1",
  port: Number(process.env.PORT) || 8787
};

const mimeTypes = new Map([
  [".html", "text/html; charset=utf-8"],
  [".css", "text/css; charset=utf-8"],
  [".js", "text/javascript; charset=utf-8"],
  [".json", "application/json; charset=utf-8"],
  [".svg", "image/svg+xml"],
  [".png", "image/png"],
  [".jpg", "image/jpeg"],
  [".jpeg", "image/jpeg"],
  [".webp", "image/webp"]
]);

function getAllowedOrigin(request) {
  const origin = request.headers.origin;
  if (!origin) {
    return "*";
  }
  if (origin.startsWith("chrome-extension://")) {
    return origin;
  }
  return null;
}

function setApiHeaders(request, response) {
  const allowedOrigin = getAllowedOrigin(request);
  if (allowedOrigin) {
    response.setHeader("Access-Control-Allow-Origin", allowedOrigin);
    response.setHeader("Vary", "Origin");
  }
  response.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  response.setHeader("Access-Control-Allow-Headers", "Content-Type");
  response.setHeader("Access-Control-Max-Age", "600");
}

function sendJson(response, statusCode, payload) {
  const body = JSON.stringify(payload);
  response.writeHead(statusCode, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(body),
    "Cache-Control": "no-store"
  });
  response.end(body);
}

async function readJsonBody(request) {
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > 256 * 1024) {
      throw new Error("请求体过大");
    }
    chunks.push(chunk);
  }
  if (chunks.length === 0) {
    throw new Error("请求体为空");
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    throw new Error("请求体不是有效 JSON");
  }
}

async function serveDemo(requestPath, response) {
  if (requestPath === "/" || requestPath === "/demo" || requestPath === "/demo/") {
    response.writeHead(302, { Location: "/demo/quiz.html" });
    response.end();
    return;
  }

  if (!requestPath.startsWith("/demo/")) {
    sendJson(response, 404, { ok: false, error: "Not found" });
    return;
  }

  const relativePath = decodeURIComponent(requestPath.slice("/demo/".length));
  const resolvedPath = path.resolve(demoRoot, relativePath);
  if (!resolvedPath.startsWith(`${demoRoot}${path.sep}`) && resolvedPath !== demoRoot) {
    sendJson(response, 403, { ok: false, error: "Forbidden" });
    return;
  }

  let fileStat;
  try {
    fileStat = await stat(resolvedPath);
  } catch {
    sendJson(response, 404, { ok: false, error: "Not found" });
    return;
  }
  if (!fileStat.isFile()) {
    sendJson(response, 404, { ok: false, error: "Not found" });
    return;
  }

  const body = await readFile(resolvedPath);
  response.writeHead(200, {
    "Content-Type": mimeTypes.get(path.extname(resolvedPath).toLowerCase()) || "application/octet-stream",
    "Content-Length": body.length,
    "Cache-Control": "no-store"
  });
  response.end(body);
}

const server = createServer(async (request, response) => {
  const requestUrl = new URL(request.url, `http://${request.headers.host || "127.0.0.1"}`);
  const requestPath = requestUrl.pathname;

  if (requestPath.startsWith("/api/")) {
    setApiHeaders(request, response);
    if (!getAllowedOrigin(request)) {
      sendJson(response, 403, { ok: false, error: "Origin not allowed" });
      return;
    }
  }

  if (request.method === "OPTIONS") {
    response.writeHead(204);
    response.end();
    return;
  }

  if (request.method === "GET" && requestPath === "/api/health") {
    sendJson(response, 200, {
      ok: true,
      mode: config.mockMode ? "mock" : "live",
      model: config.mockMode ? "local-demo" : config.model,
      reasoningEffort: config.mockMode ? "" : config.reasoningEffort,
      configured: !config.mockMode
    });
    return;
  }

  if (request.method === "GET" && requestPath === "/api/assignments") {
    const assignments = queryAssignments({
      academicYear: requestUrl.searchParams.get("academicYear"),
      status: requestUrl.searchParams.get("status"),
      courseId: requestUrl.searchParams.get("courseId"),
      submittable: requestUrl.searchParams.has("submittable")
        ? requestUrl.searchParams.get("submittable") === "true"
        : undefined
    });
    sendJson(response, 200, {
      ok: true,
      academicYear: getAssignmentSnapshot().academicYear,
      syncedAt: getAssignmentSnapshot().syncedAt,
      assignments
    });
    return;
  }

  if (request.method === "GET" && requestPath === "/api/assignments/summary") {
    sendJson(response, 200, getAssignmentSummary({
      academicYear: requestUrl.searchParams.get("academicYear"),
      status: requestUrl.searchParams.get("status"),
      submittable: requestUrl.searchParams.has("submittable")
        ? requestUrl.searchParams.get("submittable") === "true"
        : undefined
    }));
    return;
  }

  if (request.method === "POST" && requestPath === "/api/assignments/sync") {
    try {
      const body = await readJsonBody(request);
      const stored = await saveAssignmentSnapshot(body);
      sendJson(response, 200, {
        ok: true,
        syncedAt: stored.syncedAt,
        academicYear: stored.academicYear,
        coursesScanned: stored.coursesScanned,
        assignmentCount: stored.assignments.length,
        unfinishedCount: stored.assignments.filter((item) => item.status === "unfinished").length
      });
    } catch (error) {
      sendJson(response, 400, {
        ok: false,
        error: error instanceof Error ? error.message : "作业同步失败"
      });
    }
    return;
  }

  if (request.method === "POST" && requestPath === "/api/solve") {
    try {
      const body = await readJsonBody(request);
      const question = normalizeQuestion(body.question);
      const answer = await solveQuestion(question, config);
      sendJson(response, 200, { ok: true, answer });
    } catch (error) {
      sendJson(response, 400, {
        ok: false,
        error: error instanceof Error ? error.message : "答题失败"
      });
    }
    return;
  }

  if (request.method === "GET") {
    try {
      await serveDemo(requestPath, response);
    } catch (error) {
      sendJson(response, 500, {
        ok: false,
        error: error instanceof Error ? error.message : "读取页面失败"
      });
    }
    return;
  }

  sendJson(response, 405, { ok: false, error: "Method not allowed" });
});

server.listen(config.port, config.host, () => {
  console.log(`AI Quiz Assistant: http://${config.host}:${config.port}`);
  console.log(`Mode: ${config.mockMode ? "mock (演示答案)" : `live (${config.model})`}`);
  console.log(`Demo: http://${config.host}:${config.port}/demo/quiz.html`);
});

function shutdown() {
  server.close(() => process.exit(0));
}

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
