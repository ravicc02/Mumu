const BASE_URL = "http://127.0.0.1:8877";

async function request(path, options = {}) {
  const response = await fetch(`${BASE_URL}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {})
    },
    ...options
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || payload.error || `请求失败 ${response.status}`);
  }
  return payload;
}

export const api = {
  health: () => request("/api/health"),
  settings: () => request("/api/settings"),
  modelConfig: () => request("/api/model-config"),
  saveModelConfig: (config) => request("/api/model-config", {
    method: "PUT",
    body: JSON.stringify(config)
  }),
  fetchModels: (config) => request("/api/model-config/fetch-models", {
    method: "POST",
    body: JSON.stringify(config)
  }),
  testModel: (config) => request("/api/model-config/test", {
    method: "POST",
    body: JSON.stringify(config)
  }),
  weixinQr: () => request("/api/connectors/weixin/qrcode", { method: "POST" }),
  weixinStatus: (code, verifyCode = "") => request("/api/connectors/weixin/status", {
    method: "POST",
    body: JSON.stringify({ code, verify_code: verifyCode })
  }),
  weixinRefresh: () => request("/api/connectors/weixin/refresh", {
    method: "POST"
  }),
  accounts: () => request("/api/accounts"),
  platforms: () => request("/api/platforms"),
  createAccount: (name, platform = "chaoxing") => request("/api/accounts", {
    method: "POST",
    body: JSON.stringify({ name, platform })
  }),
  renameAccount: (accountId, name) => request(`/api/accounts/${accountId}`, {
    method: "PATCH",
    body: JSON.stringify({ name })
  }),
  updateCollectionSchedule: (accountId, config) => request(
    `/api/accounts/${accountId}/collection-schedule`,
    {
      method: "PATCH",
      body: JSON.stringify(config)
    }
  ),
  deleteAccount: (accountId) => request(`/api/accounts/${accountId}`, {
    method: "DELETE"
  }),
  login: (accountId) => request(`/api/accounts/${accountId}/login`, {
    method: "POST"
  }),
  loginStatus: (accountId) => request(`/api/accounts/${accountId}/login-status`),
  refreshSession: (accountId) => request(
    `/api/accounts/${accountId}/refresh-session`,
    { method: "POST" }
  ),
  preview: (accountId, reload = false) => request(
    `/api/accounts/${accountId}/preview?reload=${reload ? "true" : "false"}`
  ),
  openBrowser: (accountId) => request(
    `/api/accounts/${accountId}/open-browser`,
    { method: "POST" }
  ),
  openAssignment: (assignmentId) => request(
    `/api/assignments/${assignmentId}/open`,
    { method: "POST" }
  ),
  collect: (accountId, academicYear) => request(
    `/api/accounts/${accountId}/collect?academic_year=${academicYear}`,
    { method: "POST" }
  ),
  collectionRuns: (accountId, limit = 20) => request(
    `/api/accounts/${accountId}/collection-runs?limit=${limit}`
  ),
  collectionRun: (runId) => request(`/api/collection-runs/${runId}`),
  assignments: ({ accountId, academicYear, submittable = true }) => {
    const params = new URLSearchParams();
    if (accountId) params.set("account_id", accountId);
    if (academicYear) params.set("academic_year", academicYear);
    params.set("submittable", String(submittable));
    return request(`/api/assignments?${params}`);
  },
  semesters: (accountId) => request(
    `/api/semesters${accountId ? `?account_id=${accountId}` : ""}`
  ),
  createJob: (assignmentId, autoSubmit, interactive = true) => request("/api/jobs", {
    method: "POST",
    body: JSON.stringify({
      assignment_id: assignmentId,
      auto_submit: autoSubmit,
      interactive
    })
  }),
  jobs: () => request("/api/jobs"),
  job: (jobId) => request(`/api/jobs/${jobId}`),
  pauseJob: (jobId) => request(`/api/jobs/${jobId}/pause`, { method: "POST" }),
  resumeJob: (jobId) => request(`/api/jobs/${jobId}/resume`, { method: "POST" }),
  stopJob: (jobId) => request(`/api/jobs/${jobId}/stop`, { method: "POST" }),
  focusJobBrowser: (jobId) => request(
    `/api/jobs/${jobId}/focus-browser`,
    { method: "POST" }
  ),
  jobSnapshot: (jobId) => request(`/api/jobs/${jobId}/snapshot`),
  finishIntervention: (jobId) => request(
    `/api/jobs/${jobId}/finish-intervention`,
    { method: "POST" }
  ),
  retryJob: (jobId) => request(`/api/jobs/${jobId}/retry`, { method: "POST" }),
  verifySubmission: (jobId, status, note = "") => request(
    `/api/jobs/${jobId}/submission-status`,
    {
      method: "POST",
      body: JSON.stringify({ status, note })
    }
  ),
  events: (jobId) => request(`/api/jobs/${jobId}/events`),
  artifacts: (jobId) => request(`/api/jobs/${jobId}/artifacts`),
  testNotification: () => request("/api/notifications/test", {
    method: "POST",
    body: JSON.stringify({ message: "AI Assignment Studio 测试通知" })
  }),
  wsUrl: (jobId) => `ws://127.0.0.1:8877/ws/jobs/${jobId}`
};
