import { API_BASE as BASE_URL, artifactUrl } from "./lib/backend-url.js";

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      ...options,
      headers: { "Content-Type": "application/json", ...(options.headers || {}) }
    });
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new Error("无法连接本地服务（8877），请检查服务是否正在运行。", { cause: error });
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = Array.isArray(payload.detail)
      ? payload.detail.map(item => item.msg || "参数无效").join("；")
      : payload.detail;
    throw new Error(detail || payload.error || `请求失败 ${response.status}`);
  }
  return payload;
}

async function upload(path, file) {
  const form = new FormData();
  form.append('file', file);
  let response;
  try { response = await fetch(`${BASE_URL}${path}`, { method: 'POST', body: form }); }
  catch (error) { throw new Error('无法连接本地服务，请检查服务是否正在运行。', { cause: error }); }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || payload.error || `上传失败 ${response.status}`);
  return payload;
}

export const api = {
  attendanceBatches: () => request('/api/attendance/batches'),
  attendanceBatch: id => request('/api/attendance/batches/' + encodeURIComponent(id)),
  createAttendanceBatch: ids => request('/api/attendance/batches', {method:'POST',body:JSON.stringify({activity_ids:ids})}),
  attendanceAction: (id, action, inputs = {}) => request('/api/attendance/batches/' + encodeURIComponent(id) + '/actions/' + action, {method:'POST',body:JSON.stringify(inputs)}),
  attendanceQrGroups: () => request('/api/attendance/qr-groups'),
  createAttendanceQrGroup: options => request('/api/attendance/qr-groups', {method:'POST',body:JSON.stringify(options)}),
  revokeAttendanceQrGroup: id => request('/api/attendance/qr-groups/' + encodeURIComponent(id), {method:'DELETE'}),
  renameAttendanceQrGroup: (id, name) => request('/api/attendance/qr-groups/' + encodeURIComponent(id), {method:'PATCH',body:JSON.stringify({name})}),
  attendanceProfile: id => request('/api/attendance/accounts/' + encodeURIComponent(id) + '/profile'),
  uploadAttendanceAsset: (id, kind, file) => upload('/api/attendance/accounts/' + encodeURIComponent(id) + '/assets/' + kind, file),
  deleteAttendanceAsset: (id, kind) => request('/api/attendance/accounts/' + encodeURIComponent(id) + '/assets/' + kind, {method:'DELETE'}),
  attendanceAssetUrl: (id, kind, version = '') => `${BASE_URL}/api/attendance/accounts/${encodeURIComponent(id)}/assets/${kind}${version ? '?v=' + encodeURIComponent(version) : ''}`,
  attendanceLocation: () => request('/api/attendance/location'),
  saveAttendanceLocation: location => request('/api/attendance/location', {method:'PUT',body:JSON.stringify(location)}),
  deleteAttendanceLocation: () => request('/api/attendance/location', {method:'DELETE'}),
  attendanceAutoGroups: () => request('/api/attendance/auto-groups'),
  createAttendanceAutoGroup: options => request('/api/attendance/auto-groups', {method:'POST',body:JSON.stringify(options)}),
  updateAttendanceAutoGroup: (id, options) => request('/api/attendance/auto-groups/' + encodeURIComponent(id), {method:'PATCH',body:JSON.stringify(options)}),
  deleteAttendanceAutoGroup: id => request('/api/attendance/auto-groups/' + encodeURIComponent(id), {method:'DELETE'}),
  attendanceRelay: () => request('/api/attendance/relay'),
  testAttendanceRelay: options => request('/api/attendance/relay/test', {method:'POST',body:JSON.stringify(options)}),
  saveAttendanceRelay: options => request('/api/attendance/relay', {method:'PUT',body:JSON.stringify(options)}),
  artifactUrl,
  learningRun: id => request("/api/learning/runs/" + encodeURIComponent(id)),
  browserSession: (kind, id) => request(kind === "assignment" ? "/api/jobs/" + id + "/browser/session" : "/api/learning/runs/" + id + "/browser/session", { method: "POST" }),
  browserUrl: (kind, id) => BASE_URL.replace(/^http/, "ws") + (kind === "assignment" ? "/api/jobs/" + id + "/browser" : "/api/learning/runs/" + id + "/browser"),
  diagnosticInfo: async (kind, id) => {
    try { return await request(`/api/diagnostics/${kind}/${id}`); }
    catch (error) { if (error.message === "Not Found") throw new Error("当前后端尚未加载诊断接口，请在任务结束后重启应用。"); throw error; }
  },
  exportDiagnostics: async (kind, id, options = {}) => {
    let response;
    try { response = await fetch(`${BASE_URL}/api/diagnostics/${kind}/${id}/export`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(options) }); }
    catch { throw new Error("无法连接本地服务，请检查服务是否正在运行。"); }
    if (!response.ok) { const error = await response.json().catch(() => ({})); throw new Error(error.detail || `诊断导出失败（${response.status}）`); }
    if (!response.headers.get("Content-Type")?.includes("application/zip")) throw new Error("服务返回的诊断包格式不正确，请更新本地服务后重试。");
    return { blob: await response.blob(), filename: `diagnostic-${kind}-${id.slice(0,16)}.zip` };
  },
  learningCourses: (accountId, year) => request(`/api/learning/courses?account_id=${accountId}&academic_year=${year}`),
  collectLearningCourses: (accountId, year) => request(`/api/learning/accounts/${accountId}/collect?academic_year=${year}`, { method: "POST" }),
  learningRuns: accountId => request(`/api/learning/runs${accountId ? `?account_id=${encodeURIComponent(accountId)}` : ""}`),
  deleteTask: task => request(task.task_kind === "learning" ? `/api/learning/runs/${encodeURIComponent(task.id)}` : `/api/jobs/${encodeURIComponent(task.id)}`, { method: "DELETE" }),
  createLearningRun: courseId => request("/api/learning/runs", { method: "POST", body: JSON.stringify({ course_id: courseId }) }),
  controlLearningRun: (id, action) => request(`/api/learning/runs/${id}/${action}`, { method: "POST" }),
  learningEvents: (id, cursor = 0) => request(`/api/learning/runs/${id}/events?after_id=${cursor}`),
  learningSnapshot: id => request(`/api/learning/runs/${id}/snapshot`),
  learningArtifacts: id => request(`/api/learning/runs/${id}/artifacts`),
  learningArtifactUrl: id => `${BASE_URL}/api/learning/artifacts/${id}`,
  learningBrowserSession: id => request(`/api/learning/runs/${id}/browser/session`, { method: "POST" }),
  learningBrowserUrl: id => `${BASE_URL.replace(/^http/, "ws")}/api/learning/runs/${id}/browser`,
  health: () => request("/api/health"),
  settings: () => request("/api/settings"),
  onboarding: () => request("/api/onboarding"),
  completeOnboarding: choices => request("/api/onboarding", { method: "PUT", body: JSON.stringify(choices) }),
  setupEnvironment: () => request("/api/setup/environment"),
  openLogDirectory: async () => {
    try { return await request("/api/system/open-log-directory", { method: "POST" }); }
    catch (error) {
      if (error.message === "Not Found") throw new Error("请在任务结束后重启本地服务，启用打开日志目录功能。");
      throw error;
    }
  },
  preferences: () => request("/api/preferences"),
  savePreferences: config => request("/api/preferences", { method: "PATCH", body: JSON.stringify(config) }),
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
  weixinConnector: () => request("/api/connectors/weixin"),
  updateWeixinPreferences: config => request("/api/connectors/weixin/preferences", {
    method: "PATCH", body: JSON.stringify(config)
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
  accountAvatarUrl: account => BASE_URL + '/api/accounts/' + encodeURIComponent(account.id) + '/avatar?v=' + encodeURIComponent(account.identity_verified_at || ''),
  platformCourses: id => request('/api/accounts/' + encodeURIComponent(id) + '/courses'),
  collectPlatformCourses: id => request('/api/accounts/' + encodeURIComponent(id) + '/courses/collect', { method: 'POST' }),
  selectPlatformCourses: (id,courseIds) => request('/api/accounts/' + encodeURIComponent(id) + '/course-selections', { method: 'PUT', body: JSON.stringify({course_ids:courseIds}) }),
  createAccount: (name, platform = "chaoxing", entry_url = "") => request("/api/accounts", {
    method: "POST",
    body: JSON.stringify({ name, platform, ...(entry_url ? { entry_url } : {}) })
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
  confirmAuthorization: id => request("/api/accounts/" + id + "/confirm-authorization", { method: "POST" }),
  addLearningCourse: data => request("/api/learning/courses", { method: "POST", body: JSON.stringify(data) }),
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
  inspectAssignment: id => request(`/api/assignments/${id}/inspect`, { method: "POST" }),
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
  retryJob: (jobId, autoSubmit = false) => request(`/api/jobs/${jobId}/retry`, {
    method: "POST", body: JSON.stringify({ auto_submit: autoSubmit })
  }),
  continueJob: (jobId) => request(`/api/jobs/${jobId}/continue`, { method: "POST" }),
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
    body: JSON.stringify({ message: "Mumu 测试通知" })
  }),
  wsUrl: (jobId, afterId = 0) => `${BASE_URL.replace(/^http/, 'ws')}/ws/jobs/${jobId}?after_id=${afterId}`
};
