<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import QRCode from "qrcode";
import { api } from "./api";

const navItems = [
  { id: "dashboard", label: "面板" },
  { id: "platforms", label: "平台" },
  { id: "connectors", label: "连接器" },
  { id: "settings", label: "设置" }
];
const connectors = [
  { id: "weixin", name: "微信机器人", detail: "iLink Bot 扫码绑定", available: true },
  { id: "feishu", name: "飞书机器人", detail: "Webhook / 应用消息", available: false },
  { id: "dingtalk", name: "钉钉机器人", detail: "群机器人 Webhook", available: false },
  { id: "telegram", name: "Telegram", detail: "Bot API", available: false },
  { id: "slack", name: "Slack", detail: "Incoming Webhook", available: false },
  { id: "email", name: "电子邮件", detail: "SMTP 完成通知", available: false }
];

const activePage = ref("dashboard");
const accounts = ref([]);
const assignments = ref([]);
const semesters = ref([]);
const jobs = ref([]);
const events = ref([]);
const artifacts = ref([]);
const collectionRun = ref(null);
const collectionRuns = ref([]);
const health = ref(null);
const settingsInfo = ref(null);
const platforms = ref([]);
const selectedAccountId = ref("");
const selectedSemester = ref("");
const selectedJobId = ref("");
const accountName = ref("");
const notice = ref("");
const busy = ref(false);
const collecting = ref(false);
const error = ref("");
const screenshot = ref("");
const profilePreview = ref("");
const profilePreviewUrl = ref("");
const profilePreviewTitle = ref("");
const previewLoading = ref(false);
const autoSubmit = ref(false);
const interactiveBrowser = ref(true);
const selectedAssignmentIds = ref([]);
const batchCreating = ref(false);
const openingAssignmentId = ref("");
const socketConnected = ref(false);
const launchConfirmOpen = ref(false);
const pendingLaunch = ref({ kind: "", assignments: [] });
const accountModalOpen = ref(false);
const accountForm = ref({ name: "", platform: "chaoxing" });
const accountMenuId = ref("");
const renameModalOpen = ref(false);
const renameAccountId = ref("");
const renameValue = ref("");
const deleteModalOpen = ref(false);
const deleteAccountId = ref("");
const deleteAccountName = ref("");
const weixinModalOpen = ref(false);
const weixinQr = ref("");
const weixinCode = ref("");
const weixinStatus = ref("idle");
const weixinMessage = ref("");
const weixinVerifyCode = ref("");
const modelModalOpen = ref(false);
const modelForm = ref({
  provider_name: "ssh",
  base_url: "",
  api_key: "",
  models: [],
  reasoning_effort: "high"
});
const modelCatalog = ref([]);
const newModelName = ref("");
const modelMessage = ref("");
const verificationBusy = ref(false);
let socket = null;
let socketReconnectTimer = null;
let socketGeneration = 0;
let loginTimer = null;
let weixinTimer = null;
let profilePreviewTimer = null;
let collectionPollTimer = null;
let connectionTimer = null;
let connecting = false;
let appUnmounted = false;

const activeJobStatuses = new Set(["queued", "running", "paused", "stopping"]);

const selectedAccount = computed(() => (
  accounts.value.find((account) => account.id === selectedAccountId.value) || null
));

const unauthorizedAccount = computed(() => {
  if (selectedAccount.value && selectedAccount.value.status !== "ready") {
    return selectedAccount.value;
  }
  return accounts.value.find((account) => account.status !== "ready") || null;
});

const filteredAssignments = computed(() => assignments.value.filter((assignment) => (
  !selectedSemester.value || assignment.semester === selectedSemester.value
)));

const allFilteredSelected = computed(() => (
  filteredAssignments.value.length > 0
  && filteredAssignments.value.every((assignment) => (
    selectedAssignmentIds.value.includes(assignment.id)
  ))
));

const selectedJob = computed(() => (
  jobs.value.find((job) => job.id === selectedJobId.value) || null
));

const selectedAccountActiveJob = computed(() => (
  jobs.value.find((job) => (
    job.account_id === selectedAccountId.value
    && activeJobStatuses.has(job.status)
  )) || null
));

const groupedAssignments = computed(() => {
  const groups = new Map();
  for (const assignment of filteredAssignments.value) {
    const key = `${assignment.course_name}_${assignment.class_id}`;
    if (!groups.has(key)) {
      groups.set(key, {
        courseName: assignment.course_name,
        semester: assignment.semester,
        items: []
      });
    }
    groups.get(key).items.push(assignment);
  }
  return [...groups.values()];
});

const visibleEvents = computed(() => events.value
  .filter((event) => (
    event.kind !== "heartbeat"
    && (event.kind !== "screenshot" || event.message)
  ))
  .slice()
  .reverse());

const currentAgentState = computed(() => (
  [...events.value]
    .reverse()
    .find((event) => !["heartbeat", "screenshot"].includes(event.kind))
  || null
));

const activeJobs = computed(() => (
  jobs.value.filter((job) => activeJobStatuses.has(job.status))
));
const activeJob = computed(() => activeJobs.value[0] || null);

const selectedJobResult = computed(() => {
  const completedEvent = [...events.value]
    .reverse()
    .find((event) => event.kind === "completed");
  return completedEvent?.message || "";
});

const jobStatusLabels = {
  queued: "排队中",
  running: "运行中",
  paused: "已暂停",
  stopping: "停止中",
  stopped: "已停止",
  completed: "执行完成",
  incomplete: "未完成",
  failed: "执行失败",
  interrupted: "已中断"
};

const submissionStatusLabels = {
  not_started: "尚未执行",
  filled_pending_review: "已填写，待人工核对",
  filled_verified: "填写内容已核对",
  submitted_unverified: "允许提交，结果未验证",
  submitted_verified: "提交已验证",
  partial_pending_review: "部分完成，待人工核对",
  not_submitted: "未提交",
  submission_unknown: "提交结果未知"
};

const accountStatusLabels = {
  new: "待授权",
  authorizing: "授权中",
  waiting: "等待登录",
  ready: "已授权",
  login_error: "授权失效"
};

const platformLabels = {
  chaoxing: "学习通"
};

function jobStatusLabel(status) {
  return jobStatusLabels[status] || status || "未知";
}

function submissionStatusLabel(status) {
  return submissionStatusLabels[status] || "状态未知";
}

function accountStatusLabel(status) {
  return accountStatusLabels[status] || status || "未知";
}

function platformLabel(platform) {
  return platformLabels[platform] || platform || "未知平台";
}

function formatLocalTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  });
}

function currentAcademicYear() {
  const today = new Date();
  return today.getMonth() >= 7 ? today.getFullYear() : today.getFullYear() - 1;
}

function collectionStatusLabel(account) {
  if (!account.auto_collect_enabled) {
    if (account.last_collection_status === "success") {
      return `上次采集成功 · ${account.last_collection_count || 0} 个可提交`;
    }
    if (account.last_collection_status === "error") {
      return "上次采集失败";
    }
  if (account.last_collection_status === "partial") {
    return `上次部分采集 · ${account.last_collection_count || 0} 个可提交`;
  }
  if (account.last_collection_status === "running") {
    return "正在采集";
  }
    return "";
  }
  if (account.last_collection_status === "success") {
    return `采集成功 · ${account.last_collection_count || 0} 个可提交`;
  }
  if (account.last_collection_status === "error") {
    return "采集失败";
  }
  if (account.last_collection_status === "partial") {
    return `部分采集 · ${account.last_collection_count || 0} 个可提交`;
  }
  if (account.last_collection_status === "running") {
    return "正在采集";
  }
  return "已启用，等待下次检测";
}

function collectionRunLabel(run) {
  if (!run) return "";
  if (run.status === "running") {
    return run.current_course
      ? `正在采集：${run.current_course}`
      : "正在读取课程与作业";
  }
  if (run.status === "partial") {
    return `部分采集：${run.failed_courses} 个课程失败`;
  }
  if (run.status === "failed") {
    return run.error || "采集失败";
  }
  return `采集完成：${run.submittable_count || 0} 个可提交`;
}

function assignmentEmptyTitle() {
  if (selectedAccount.value?.status !== "ready") {
    return "账号尚未授权";
  }
  if (!collectionRun.value) {
    return "尚未采集作业";
  }
  if (collectionRun.value.status === "running") {
    return "正在采集作业";
  }
  if (collectionRun.value.status === "failed") {
    return "采集失败";
  }
  if (collectionRun.value.status === "partial") {
    return "本轮未发现可提交作业";
  }
  return "暂无可提交作业";
}

function assignmentEmptyDescription() {
  if (selectedAccount.value?.status !== "ready") {
    return "通过账号右侧“...”菜单刷新 Cookie 完成授权。";
  }
  if (!collectionRun.value) {
    return "点击“采集”读取当前学年仍可提交的作业。";
  }
  if (collectionRun.value.status === "running") {
    return "采集完成后作业列表会自动更新。";
  }
  if (collectionRun.value.status === "failed") {
    return collectionRun.value.error || "请检查登录状态和网络后重试。";
  }
  if (collectionRun.value.status === "partial") {
    return `有 ${collectionRun.value.failed_courses} 个课程采集失败，请展开上方失败详情。`;
  }
  return "本轮采集没有发现仍可提交的未完成作业。";
}

function nextCollectionLabel(account) {
  if (account.last_collection_status === "running") {
    return "正在执行";
  }
  if (!account.auto_collect_enabled || !account.last_collection_at) {
    return "";
  }
  const last = new Date(account.last_collection_at);
  if (Number.isNaN(last.getTime())) return "";
  const interval = Number(account.auto_collect_interval_minutes || 360);
  const next = new Date(last.getTime() + interval * 60 * 1000);
  if (next.getTime() <= Date.now()) {
    return "下次采集：即将执行";
  }
  return `下次采集：${formatLocalTime(next.toISOString())}`;
}

function latestJobForAssignment(assignmentId) {
  return jobs.value.find((job) => job.assignment_id === assignmentId) || null;
}

function queuePosition(job) {
  if (!job || job.status !== "queued") return 0;
  const queued = jobs.value
    .filter((item) => (
      item.account_id === job.account_id && item.status === "queued"
    ))
    .slice()
    .sort((left, right) => (
      new Date(left.created_at).getTime() - new Date(right.created_at).getTime()
    ));
  return queued.findIndex((item) => item.id === job.id) + 1;
}

function assignmentJobLabel(assignmentId) {
  const job = latestJobForAssignment(assignmentId);
  if (!job) return "启动 Agent";
  if (activeJobStatuses.has(job.status)) return "查看任务";
  return "再次运行";
}

function isActiveJob(job) {
  return Boolean(job && activeJobStatuses.has(job.status));
}

function hasActiveJobForAccount(accountId) {
  return jobs.value.some((job) => (
    job.account_id === accountId && activeJobStatuses.has(job.status)
  ));
}

function artifactLabel(kind) {
  return {
    screenshot: "步骤截图",
    final_screenshot: "最终截图",
    result_json: "运行结果 JSON"
  }[kind] || kind;
}

function actionSummary(actions) {
  if (!Array.isArray(actions) || actions.length === 0) return "";
  const labels = {
    ClickActionModel: "点击",
    InputActionModel: "输入",
    NavigateActionModel: "打开页面",
    EvaluateActionModel: "检查页面",
    ScrollActionModel: "滚动",
    WaitActionModel: "等待",
    DoneActionModel: "结束任务",
    SendKeysActionModel: "按键",
    UploadFileActionModel: "上传文件",
    SelectDropdownActionModel: "选择下拉项"
  };
  return actions.map((action) => labels[action] || action).join("、");
}

function deadlineState(deadlineText) {
  if (!deadlineText) {
    return { label: "截止时间未知", className: "unknown" };
  }
  const match = String(deadlineText).match(
    /至\s*(\d{2})-(\d{2})\s+(\d{2}):(\d{2})/
  );
  if (!match) {
    return { label: "截止时间未知", className: "unknown" };
  }
  const [, month, day, hour, minute] = match;
  const now = new Date();
  let deadline = new Date(
    now.getFullYear(),
    Number(month) - 1,
    Number(day),
    Number(hour),
    Number(minute)
  );
  if (deadline.getTime() - now.getTime() > 180 * 24 * 60 * 60 * 1000) {
    deadline = new Date(
      now.getFullYear() - 1,
      Number(month) - 1,
      Number(day),
      Number(hour),
      Number(minute)
    );
  }
  const remaining = deadline.getTime() - now.getTime();
  if (remaining < 0) {
    return {
      label: "已过期但仍可提交",
      className: "overdue"
    };
  }
  if (remaining < 48 * 60 * 60 * 1000) {
    return { label: "即将截止", className: "soon" };
  }
  return { label: "可提交", className: "open" };
}

const connectionLabel = computed(() => {
  if (!health.value) return "后端离线";
  return health.value.ai_configured ? `${health.value.model} 已配置` : "AI 未配置";
});

function setError(value) {
  error.value = value instanceof Error ? value.message : String(value || "");
}

async function loadHealth({ silent = false } = {}) {
  try {
    health.value = await api.health();
  } catch (loadError) {
    health.value = null;
    if (!silent) setError(loadError);
  }
}

async function loadSettings() {
  try {
    settingsInfo.value = await api.settings();
  } catch (loadError) {
    setError(loadError);
  }
}

async function loadPlatforms() {
  platforms.value = await api.platforms();
}

async function loadModelConfig() {
  const config = await api.modelConfig();
  modelForm.value = {
    provider_name: config.provider_name || "custom",
    base_url: config.base_url || "",
    api_key: "",
    models: [...(config.models || [])],
    reasoning_effort: config.reasoning_effort || "high"
  };
}

async function loadAccounts() {
  accounts.value = await api.accounts();
  if (!selectedAccountId.value && accounts.value.length > 0) {
    selectedAccountId.value = accounts.value[0].id;
  }
}

async function refreshProfilePreview(reload = false) {
  if (!selectedAccountId.value) {
    profilePreview.value = "";
    profilePreviewUrl.value = "";
    profilePreviewTitle.value = "";
    previewLoading.value = false;
    return;
  }
  previewLoading.value = true;
  if (!profilePreview.value) {
    profilePreviewTitle.value = "正在加载当前账号页面...";
  }
  try {
    const preview = await api.preview(selectedAccountId.value, reload);
    profilePreview.value = preview.image;
    profilePreviewUrl.value = preview.url;
    profilePreviewTitle.value = preview.title;
  } catch (previewError) {
    profilePreview.value = "";
    profilePreviewUrl.value = "";
    profilePreviewTitle.value = previewError.message;
  } finally {
    previewLoading.value = false;
  }
}

function startProfilePreview() {
  clearInterval(profilePreviewTimer);
  if (activePage.value !== "platforms" || !selectedAccountId.value) {
    return;
  }
  refreshProfilePreview(true);
  profilePreviewTimer = setInterval(() => refreshProfilePreview(false), 2500);
}

function openConnector(connector) {
  if (connector.id === "weixin") {
    openWeixinBinding();
    return;
  }
  notice.value = `${connector.name} 连接器尚未开放。`;
}

async function loadAssignments() {
  const academicYear = selectedSemester.value
    ? Number(selectedSemester.value.slice(0, 4))
    : undefined;
  assignments.value = await api.assignments({
    accountId: selectedAccountId.value || undefined,
    academicYear,
    submittable: true
  });
}

async function loadCollectionRuns() {
  if (!selectedAccountId.value) {
    collectionRuns.value = [];
    collectionRun.value = null;
    return;
  }
  collectionRuns.value = await api.collectionRuns(selectedAccountId.value, 10);
  collectionRun.value = collectionRuns.value[0] || null;
}

async function loadSemesters() {
  semesters.value = await api.semesters(selectedAccountId.value || undefined);
  if (!selectedSemester.value && semesters.value.length > 0) {
    selectedSemester.value = semesters.value[0];
  }
  if (selectedSemester.value && !semesters.value.includes(selectedSemester.value)) {
    selectedSemester.value = semesters.value[0] || "";
  }
}

async function loadJobs() {
  jobs.value = await api.jobs();
  if (!selectedJobId.value && jobs.value.length > 0) {
    await selectJob(jobs.value[0].id);
  }
}

async function refreshWorkspace() {
  await Promise.all([
    loadAccounts(),
    loadJobs(),
    loadSettings(),
    loadPlatforms(),
    loadModelConfig()
  ]);
  if (selectedAccountId.value) {
    await Promise.all([
      loadSemesters(),
      loadAssignments(),
      loadCollectionRuns()
    ]);
  }
}

async function connectBackend() {
  if (connecting) return;
  connecting = true;
  try {
    for (let attempt = 0; attempt < 20; attempt += 1) {
      await loadHealth();
      if (health.value) {
        await refreshWorkspace();
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
  } finally {
    connecting = false;
  }
}

async function createAccount() {
  if (!accountForm.value.name.trim()) return;
  busy.value = true;
  try {
    const account = await api.createAccount(
      accountForm.value.name.trim(),
      accountForm.value.platform
    );
    await loadAccounts();
    selectedAccountId.value = account.id;
    accountModalOpen.value = false;
    accountName.value = "";
    notice.value = "账号已添加。点击账号右侧“...”→“刷新 Cookie”完成登录授权。";
  } catch (createError) {
    setError(createError);
  } finally {
    busy.value = false;
  }
}

async function startLogin() {
  if (!selectedAccountId.value) return;
  busy.value = true;
  notice.value = "正在打开登录浏览器...";
  try {
    await api.login(selectedAccountId.value);
    await loadAccounts();
    clearInterval(loginTimer);
    loginTimer = setInterval(async () => {
      const status = await api.loginStatus(selectedAccountId.value);
      notice.value = status.message || status.status;
      if (["success", "error", "idle"].includes(status.status)) {
        clearInterval(loginTimer);
        loginTimer = null;
        await loadAccounts();
      }
    }, 1500);
  } catch (loginError) {
    setError(loginError);
  } finally {
    busy.value = false;
  }
}

async function authorizeAccount(account) {
  selectedAccountId.value = account.id;
  await startLogin();
}

async function refreshAccountCookie(account) {
  accountMenuId.value = "";
  busy.value = true;
  try {
    const result = await api.refreshSession(account.id);
    notice.value = result.message;
    await loadAccounts();
    if (!result.ok) {
      await authorizeAccount(account);
    }
    await refreshProfilePreview(true);
  } catch (refreshError) {
    setError(refreshError);
  } finally {
    busy.value = false;
  }
}

function toggleAccountMenu(accountId) {
  accountMenuId.value = accountMenuId.value === accountId ? "" : accountId;
}

function closeAccountMenuOnOutsideClick(event) {
  if (!accountMenuId.value) return;
  if (event.target instanceof Element && event.target.closest(".account-menu-wrap")) {
    return;
  }
  accountMenuId.value = "";
}

function closeAccountMenuOnEscape(event) {
  if (event.key === "Escape") {
    if (modelModalOpen.value) {
      modelModalOpen.value = false;
      return;
    }
    if (weixinModalOpen.value) {
      weixinModalOpen.value = false;
      return;
    }
    if (deleteModalOpen.value) {
      deleteModalOpen.value = false;
      return;
    }
    if (renameModalOpen.value) {
      renameModalOpen.value = false;
      return;
    }
    if (accountModalOpen.value) {
      accountModalOpen.value = false;
      return;
    }
    if (launchConfirmOpen.value) {
      launchConfirmOpen.value = false;
      return;
    }
    accountMenuId.value = "";
    return;
  }
  if (event.key === "Tab") {
    const modal = document.querySelector(".modal-backdrop .modal-card");
    if (!modal) return;
    const focusable = [...modal.querySelectorAll(
      "button:not(:disabled), input:not(:disabled), select:not(:disabled), "
      + "textarea:not(:disabled), a[href], [tabindex]:not([tabindex='-1'])"
    )];
    if (focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }
}

function openRenameAccount(account) {
  accountMenuId.value = "";
  renameAccountId.value = account.id;
  renameValue.value = account.name;
  renameModalOpen.value = true;
}

async function saveRenameAccount() {
  if (!renameValue.value.trim()) return;
  try {
    await api.renameAccount(renameAccountId.value, renameValue.value.trim());
    renameModalOpen.value = false;
    await loadAccounts();
    notice.value = "账号已重命名。";
  } catch (renameError) {
    setError(renameError);
  }
}

function openDeleteAccount(account) {
  accountMenuId.value = "";
  deleteAccountId.value = account.id;
  deleteAccountName.value = account.name;
  deleteModalOpen.value = true;
}

async function confirmDeleteAccount() {
  try {
    await api.deleteAccount(deleteAccountId.value);
    if (selectedAccountId.value === deleteAccountId.value) {
      selectedAccountId.value = "";
    }
    deleteModalOpen.value = false;
    await loadAccounts();
    await loadAssignments();
    notice.value = "账号及其本地 Cookie 已删除。";
  } catch (deleteError) {
    setError(deleteError);
  }
}

async function collectAssignments() {
  if (!selectedAccountId.value) return;
  if (selectedAccount.value?.status !== "ready") {
    notice.value = "当前账号尚未授权，正在打开学习通登录。";
    await authorizeAccount(selectedAccount.value);
    return;
  }
  collecting.value = true;
  error.value = "";
  collectionRun.value = {
    status: "running",
    progress: 1,
    current_course: "",
    total_courses: 0,
    processed_courses: 0,
    failed_courses: 0
  };
  clearInterval(collectionPollTimer);
  collectionPollTimer = setInterval(async () => {
    try {
      await loadCollectionRuns();
    } catch {
      // The active request will surface the terminal collection error.
    }
  }, 1200);
  try {
    const academicYear = selectedSemester.value
      ? Number(selectedSemester.value.slice(0, 4))
      : currentAcademicYear();
    const result = await api.collect(selectedAccountId.value, academicYear);
    await Promise.all([
      loadAccounts(),
      loadSemesters(),
      loadAssignments(),
      loadCollectionRuns()
    ]);
    collectionRun.value = result.finished || collectionRun.value;
    notice.value = result.status === "partial"
      ? `部分采集完成：${result.submittable} 个可提交，${result.failed_courses} 个课程失败`
      : `采集完成：${result.submittable} 个当前可提交作业`;
    activePage.value = "platforms";
  } catch (collectError) {
    setError(collectError);
    await loadCollectionRuns().catch(() => {});
  } finally {
    clearInterval(collectionPollTimer);
    collectionPollTimer = null;
    collecting.value = false;
  }
}

async function createJob(assignment) {
  busy.value = true;
  error.value = "";
  notice.value = `正在创建任务：${assignment.title}`;
  try {
    const job = await api.createJob(
      assignment.id,
      autoSubmit.value,
      interactiveBrowser.value
    );
    jobs.value = [job, ...jobs.value];
    await selectJob(job.id);
    activePage.value = "dashboard";
    notice.value = `Agent 任务已创建：${assignment.title}`;
  } catch (jobError) {
    setError(jobError);
  } finally {
    busy.value = false;
  }
}

async function startAssignmentJob(assignment) {
  const existingJob = latestJobForAssignment(assignment.id);
  if (existingJob && activeJobStatuses.has(existingJob.status)) {
    await selectJob(existingJob.id);
    activePage.value = "dashboard";
    notice.value = "已打开该作业当前运行中的任务。";
    return;
  }
  if (autoSubmit.value) {
    pendingLaunch.value = { kind: "single", assignments: [assignment] };
    launchConfirmOpen.value = true;
    return;
  }
  await createJob(assignment);
}

async function openAssignment(assignment) {
  openingAssignmentId.value = assignment.id;
  error.value = "";
  notice.value = `正在打开：${assignment.title}`;
  try {
    const result = await api.openAssignment(assignment.id);
    notice.value = result.message;
    await refreshProfilePreview(false);
  } catch (openError) {
    setError(openError);
  } finally {
    openingAssignmentId.value = "";
  }
}

function toggleAssignmentSelection(assignmentId) {
  if (selectedAssignmentIds.value.includes(assignmentId)) {
    selectedAssignmentIds.value = selectedAssignmentIds.value.filter(
      (id) => id !== assignmentId
    );
  } else {
    selectedAssignmentIds.value = [
      ...selectedAssignmentIds.value,
      assignmentId
    ];
  }
}

function toggleSelectAllAssignments() {
  if (allFilteredSelected.value) {
    selectedAssignmentIds.value = []
    return
  }
  selectedAssignmentIds.value = filteredAssignments.value.map(
    (assignment) => assignment.id
  )
}

async function createBatchJobs() {
  const candidates = filteredAssignments.value.filter((assignment) => (
    selectedAssignmentIds.value.includes(assignment.id)
  ));
  if (candidates.length === 0) {
    notice.value = "请先选择至少一个作业。"
    return
  }
  const selected = candidates.filter((assignment) => (
    !isActiveJob(latestJobForAssignment(assignment.id))
  ));
  const skipped = candidates.length - selected.length;
  if (selected.length === 0) {
    notice.value = skipped > 0
      ? `所选 ${skipped} 个作业已有运行中任务，请从作业行进入对应任务。`
      : "请先选择至少一个作业。";
    return;
  }
  if (autoSubmit.value) {
    pendingLaunch.value = { kind: "batch", assignments: selected };
    launchConfirmOpen.value = true;
    return;
  }
  await executeBatchJobs(selected, skipped);
}

async function executeBatchJobs(selected, skipped = 0) {
  batchCreating.value = true
  busy.value = true
  error.value = ""
  const createdJobs = []
  const failures = []
  try {
    for (let index = 0; index < selected.length; index += 1) {
      const assignment = selected[index]
      notice.value = `正在创建批量任务 ${index + 1}/${selected.length}：${assignment.title}`
      try {
        const job = await api.createJob(
          assignment.id,
          autoSubmit.value,
          interactiveBrowser.value
        )
        createdJobs.push(job)
      } catch (batchError) {
        failures.push(`${assignment.title}: ${batchError.message}`)
      }
    }
    jobs.value = [...createdJobs, ...jobs.value]
    selectedAssignmentIds.value = []
    if (createdJobs.length > 0) {
      await selectJob(createdJobs[0].id)
      activePage.value = "dashboard"
    }
    const skippedText = skipped ? `，跳过 ${skipped} 个已有任务` : "";
    notice.value = failures.length
      ? `已创建 ${createdJobs.length} 个任务，${failures.length} 个失败${skippedText}`
      : `已创建 ${createdJobs.length} 个 Agent 任务${skippedText}`
  } catch (batchError) {
    setError(batchError)
  } finally {
    batchCreating.value = false
    busy.value = false
  }
}

async function confirmAutoSubmitLaunch() {
  const pending = pendingLaunch.value;
  launchConfirmOpen.value = false;
  pendingLaunch.value = { kind: "", assignments: [] };
  if (pending.kind === "single" && pending.assignments[0]) {
    await createJob(pending.assignments[0]);
    return;
  }
  if (pending.kind === "batch") {
    await executeBatchJobs(pending.assignments);
  }
}

async function controlSelectedJob(action) {
  if (!selectedJobId.value) return
  const selectedId = selectedJobId.value
  try {
    let job
    if (action === "retry") {
      job = await api.retryJob(selectedId)
      jobs.value = [job, ...jobs.value]
      await selectJob(job.id)
      notice.value = "已重新创建任务"
    } else {
      const method = {
        pause: api.pauseJob,
        resume: api.resumeJob,
        stop: api.stopJob
      }[action]
      job = await method(selectedId)
      jobs.value = jobs.value.map((item) => (
        item.id === job.id ? { ...item, ...job } : item
      ))
      notice.value = {
        pause: "任务已暂停",
        resume: "任务已继续",
        stop: "正在停止任务"
      }[action]
    }
  } catch (controlError) {
    setError(controlError)
  }
}

async function verifySelectedSubmission(status) {
  if (!selectedJobId.value || verificationBusy.value) return;
  verificationBusy.value = true;
  try {
    const job = await api.verifySubmission(selectedJobId.value, status);
    jobs.value = jobs.value.map((item) => (
      item.id === job.id ? { ...item, ...job } : item
    ));
    const [eventData, artifactData] = await Promise.all([
      api.events(job.id),
      api.artifacts(job.id)
    ]);
    events.value = eventData;
    artifacts.value = artifactData;
    notice.value = status === "submitted_verified"
      ? "已人工确认作业提交成功"
      : "已更新人工核对结果";
  } catch (verificationError) {
    setError(verificationError);
  } finally {
    verificationBusy.value = false;
  }
}

async function focusAgentBrowser() {
  if (!selectedJobId.value) return;
  try {
    await api.focusJobBrowser(selectedJobId.value);
    notice.value = "已切换到 Agent 浏览器";
  } catch (focusError) {
    setError(focusError);
  }
}

async function openInteractiveBrowser() {
  if (!selectedAccountId.value) return;
  try {
    const result = await api.openBrowser(selectedAccountId.value);
    notice.value = result.message;
    await refreshProfilePreview(true);
  } catch (browserError) {
    setError(browserError);
  }
}

async function openSelectedJobAssignment() {
  if (!selectedJob.value) return;
  await openAssignment({
    id: selectedJob.value.assignment_id,
    title: selectedJob.value.assignment_title
  });
}

function closeJobSocket() {
  socketGeneration += 1;
  clearTimeout(socketReconnectTimer);
  socketReconnectTimer = null;
  if (socket) {
    socket.close();
    socket = null;
  }
  socketConnected.value = false;
}

function connectJobSocket(jobId) {
  if (!jobId || appUnmounted) return;
  const generation = ++socketGeneration;
  if (socket) {
    socket.close();
    socket = null;
  }
  const currentSocket = new WebSocket(api.wsUrl(jobId));
  socket = currentSocket;
  currentSocket.onopen = () => {
    if (generation !== socketGeneration) {
      currentSocket.close();
      return;
    }
    socketConnected.value = true;
  };
  currentSocket.onmessage = async (message) => {
    if (generation !== socketGeneration) return;
    const event = JSON.parse(message.data);
    const existingIndex = events.value.findIndex((item) => item.id === event.id);
    if (existingIndex >= 0) {
      events.value[existingIndex] = {
        ...events.value[existingIndex],
        ...event,
        data: {
          ...(events.value[existingIndex].data || {}),
          ...(event.data || {})
        }
      };
    } else {
      events.value.push(event);
    }
    if (event.data?.image) screenshot.value = event.data.image;
    if ([
      "queued",
      "status",
      "paused",
      "resumed",
      "stopping",
      "stopped",
      "completed",
      "incomplete",
      "error",
      "interrupted",
      "submission_verified",
      "submission_reviewed"
    ].includes(event.kind)) {
      const updatedJob = await api.job(jobId);
      jobs.value = [
        updatedJob,
        ...jobs.value.filter((job) => job.id !== updatedJob.id)
      ];
    }
    if ([
      "stopped",
      "completed",
      "incomplete",
      "error",
      "interrupted"
    ].includes(event.kind)) {
      artifacts.value = await api.artifacts(jobId);
    }
  };
  currentSocket.onclose = () => {
    if (generation !== socketGeneration) return;
    if (socket === currentSocket) {
      socket = null;
      socketConnected.value = false;
    }
    if (!appUnmounted && selectedJobId.value === jobId) {
      clearTimeout(socketReconnectTimer);
      socketReconnectTimer = setTimeout(() => connectJobSocket(jobId), 2000);
    }
  };
  currentSocket.onerror = () => {
    currentSocket.close();
  };
}

async function selectJob(jobId) {
  selectedJobId.value = jobId;
  events.value = [];
  artifacts.value = [];
  screenshot.value = "";
  closeJobSocket();
  try {
    const [eventData, artifactData] = await Promise.all([
      api.events(jobId),
      api.artifacts(jobId)
    ]);
    events.value = eventData;
    artifacts.value = artifactData;
    const latestImage = [...eventData].reverse().find((event) => event.data?.image);
    if (latestImage) screenshot.value = latestImage.data.image;
    if (!screenshot.value) {
      const latestScreenshot = [...artifactData].reverse().find((artifact) => (
        ["screenshot", "final_screenshot"].includes(artifact.kind)
      ));
      if (latestScreenshot) {
        screenshot.value = `http://127.0.0.1:8877/api/artifacts/${latestScreenshot.id}`;
      }
    }
  } catch (loadError) {
    setError(loadError);
  }

  connectJobSocket(jobId);
}

async function testNotification() {
  try {
    const result = await api.testNotification();
    notice.value = result.configured
      ? (result.ok ? "测试通知已发送" : "通知发送失败")
      : "尚未配置通知连接器";
  } catch (notificationError) {
    setError(notificationError);
  }
}

async function openModelModal() {
  await loadModelConfig();
  modelCatalog.value = [];
  modelMessage.value = "";
  modelModalOpen.value = true;
}

async function updateCollectionSchedule(account, patch) {
  try {
    await api.updateCollectionSchedule(account.id, patch);
    await loadAccounts();
    notice.value = `已更新 ${account.name} 的自动检测设置。`;
  } catch (scheduleError) {
    setError(scheduleError);
    await loadAccounts();
  }
}

async function fetchModelList() {
  modelMessage.value = "正在拉取模型列表...";
  try {
    const result = await api.fetchModels(modelForm.value);
    modelCatalog.value = result.models || [];
    modelMessage.value = `已获取 ${modelCatalog.value.length} 个模型`;
  } catch (fetchError) {
    modelMessage.value = fetchError.message;
  }
}

function addModel(modelName) {
  const name = String(modelName || "").trim();
  if (name && !modelForm.value.models.includes(name)) {
    modelForm.value.models.push(name);
    newModelName.value = "";
  }
}

function removeModel(index) {
  modelForm.value.models.splice(index, 1);
}

async function saveModelSettings() {
  modelMessage.value = "正在保存...";
  try {
    await api.saveModelConfig(modelForm.value);
    modelModalOpen.value = false;
    await Promise.all([loadHealth(), loadSettings()]);
    notice.value = "模型配置已保存。";
  } catch (saveError) {
    modelMessage.value = saveError.message;
  }
}

async function testModelConnection() {
  modelMessage.value = "正在测试连接...";
  try {
    await api.testModel(modelForm.value);
    modelMessage.value = "连接成功";
  } catch (testError) {
    modelMessage.value = testError.message;
  }
}

async function openWeixinBinding() {
  weixinModalOpen.value = true;
  weixinQr.value = "";
  weixinStatus.value = "loading";
  weixinMessage.value = "正在获取二维码...";
  clearInterval(weixinTimer);
  try {
    const result = await api.weixinQr();
    weixinCode.value = result.code;
    weixinQr.value = await QRCode.toDataURL(result.qr_text, {
      margin: 2,
      width: 280
    });
    weixinStatus.value = "wait";
    weixinMessage.value = "请使用微信扫码并确认授权";
    weixinTimer = setInterval(pollWeixinStatus, 2000);
  } catch (qrError) {
    weixinStatus.value = "error";
    weixinMessage.value = qrError.message;
  }
}

async function pollWeixinStatus() {
  if (!weixinCode.value) return;
  try {
    const result = await api.weixinStatus(
      weixinCode.value,
      weixinVerifyCode.value
    );
    weixinStatus.value = result.status;
    if (result.status === "scaned") {
      weixinMessage.value = "已扫码，等待手机确认";
    } else if (result.status === "need_verifycode") {
      weixinMessage.value = "请输入手机显示的验证码";
    } else if (result.status === "confirmed") {
      clearInterval(weixinTimer);
      weixinTimer = null;
      weixinMessage.value = "绑定完成。请在微信中给机器人发一条消息，然后刷新会话。";
      await loadSettings();
    }
  } catch (statusError) {
    weixinMessage.value = statusError.message;
  }
}

async function refreshWeixinSession() {
  try {
    const result = await api.weixinRefresh();
    weixinMessage.value = result.message;
    await loadSettings();
  } catch (refreshError) {
    weixinMessage.value = refreshError.message;
  }
}

async function openDataDirectory() {
  if (window.desktopApi?.openDataDirectory) {
    await window.desktopApi.openDataDirectory();
  }
}

async function openLogDirectory() {
  if (window.desktopApi?.openLogDirectory) {
    await window.desktopApi.openLogDirectory();
  }
}

function goToPage(pageId) {
  activePage.value = pageId;
}

watch(selectedAccountId, async () => {
  if (!selectedAccountId.value) return;
  selectedAssignmentIds.value = [];
  await Promise.all([
    loadSemesters(),
    loadAssignments(),
    loadCollectionRuns()
  ]);
  startProfilePreview();
});

watch(selectedSemester, () => {
  selectedAssignmentIds.value = [];
  loadAssignments();
});

watch(activePage, () => {
  startProfilePreview();
});

watch(accountModalOpen, async (isOpen) => {
  if (!isOpen) return;
  await nextTick();
  document.querySelector("#account-name-input")?.focus();
});

watch(renameModalOpen, async (isOpen) => {
  if (!isOpen) return;
  await nextTick();
  document.querySelector("#rename-account-input")?.focus();
});

watch(modelModalOpen, async (isOpen) => {
  if (!isOpen) return;
  await nextTick();
  document.querySelector("#model-provider-input")?.focus();
});

watch(launchConfirmOpen, async (isOpen) => {
  if (!isOpen) return;
  await nextTick();
  document.querySelector(
    ".modal-backdrop .modal-card .modal-actions .button"
  )?.focus();
});

onMounted(async () => {
  appUnmounted = false;
  document.addEventListener("click", closeAccountMenuOnOutsideClick);
  document.addEventListener("keydown", closeAccountMenuOnEscape);
  await connectBackend();
  connectionTimer = setInterval(async () => {
    const wasOffline = !health.value;
    await loadHealth({ silent: true });
    if (wasOffline && health.value) {
      await connectBackend();
    } else if (health.value) {
      try {
        await Promise.all([loadJobs(), loadAccounts()]);
        if (activePage.value === "platforms" && selectedAccountId.value) {
          await loadCollectionRuns();
        }
      } catch {
        // The next health check will surface a recovered or lost backend.
      }
    }
  }, 5000);
});

onUnmounted(() => {
  appUnmounted = true;
  document.removeEventListener("click", closeAccountMenuOnOutsideClick);
  document.removeEventListener("keydown", closeAccountMenuOnEscape);
  closeJobSocket();
  if (loginTimer) clearInterval(loginTimer);
  if (weixinTimer) clearInterval(weixinTimer);
  if (profilePreviewTimer) clearInterval(profilePreviewTimer);
  if (collectionPollTimer) clearInterval(collectionPollTimer);
  if (connectionTimer) clearInterval(connectionTimer);
});
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <div class="brand">
        <span class="brand-mark">AI</span>
        <h1>Assignment Studio</h1>
      </div>
      <div class="topbar-meta">
        <span class="connection-pill" :class="{ online: health?.ai_configured }">
          {{ connectionLabel }}
        </span>
      </div>
    </header>

    <div v-if="error" class="error-strip" role="alert">
      <span>{{ error }}</span>
      <button type="button" @click="error = ''">关闭</button>
    </div>

    <div class="app-body">
      <nav class="side-nav" aria-label="主导航">
        <button
          v-for="item in navItems"
          :key="item.id"
          type="button"
          class="nav-item"
          :class="{ active: activePage === item.id }"
          @click="goToPage(item.id)"
        >
          <span>{{ item.label }}</span>
        </button>
        <div class="side-nav-footer">
          <small>v0.3.0</small>
        </div>
      </nav>

      <div class="app-feedback">
        <button
          v-if="activeJob"
          class="global-job-strip"
          type="button"
          :class="activeJob.status"
          @click="selectJob(activeJob.id); activePage = 'dashboard'"
        >
          <span class="global-job-kicker">当前任务</span>
          <strong>{{ activeJob.assignment_title }}</strong>
          <span>
            {{ jobStatusLabel(activeJob.status) }}
            <template v-if="activeJobs.length > 1">
              · 另有 {{ activeJobs.length - 1 }} 个任务
            </template>
            <template v-if="activeJob.status === 'queued'">
              · 第 {{ queuePosition(activeJob) }} 位
            </template>
          </span>
          <span class="global-job-progress">{{ activeJob.progress }}%</span>
          <span class="global-job-action">返回任务</span>
        </button>
        <div v-if="notice" class="notice-line" role="status" aria-live="polite">
          {{ notice }}
        </div>
      </div>

      <main v-if="activePage === 'dashboard'" class="dashboard-layout">
        <section class="dashboard-main">
          <div class="page-heading">
            <span class="eyebrow">运行总览</span>
            <h2>任务面板</h2>
            <p>查看账号、任务和实时 Agent 执行情况。</p>
          </div>

          <div class="stat-grid">
            <article class="stat-item">
              <span>账号</span>
              <strong>{{ accounts.length }}</strong>
            </article>
            <article class="stat-item">
              <span>当前账号可提交</span>
              <strong>{{ assignments.length }}</strong>
            </article>
            <article class="stat-item">
              <span>Agent 任务</span>
              <strong>{{ jobs.length }}</strong>
            </article>
          </div>

          <div class="section-title">
            <span>最近任务</span>
            <span class="count">{{ jobs.length }}</span>
          </div>
          <div v-if="jobs.length === 0" class="empty-inline">还没有 Agent 任务。</div>
          <button
            v-for="job in jobs"
            :key="job.id"
            type="button"
            class="job-card"
            :class="{ active: job.id === selectedJobId }"
            @click="selectJob(job.id)"
          >
            <span class="job-status" :class="job.status"></span>
            <span>
              <strong>{{ job.assignment_title }}</strong>
              <small>
                {{ job.account_name || "未知账号" }} · {{ job.course_name }} ·
                第 {{ job.attempt || 1 }} 次 · {{ formatLocalTime(job.created_at) }}
              </small>
            </span>
            <span class="job-card-meta">
              <span class="job-card-status">
                {{ jobStatusLabel(job.status) }}
                <template v-if="job.status === 'queued'">
                  · 第 {{ queuePosition(job) }} 位
                </template>
              </span>
              <small>{{ submissionStatusLabel(job.submission_status) }}</small>
            </span>
          </button>
        </section>

        <aside class="agent-panel dashboard-agent-panel">
          <div class="agent-heading">
            <div>
              <span class="eyebrow">
                实时执行
                <template v-if="selectedJob?.account_name">
                  · {{ selectedJob.account_name }}
                </template>
              </span>
              <h2>{{ selectedJob?.assignment_title || "Agent 监控" }}</h2>
            </div>
            <div class="agent-heading-meta">
              <span
                v-if="selectedJob && isActiveJob(selectedJob)"
                class="socket-state"
                :class="{ online: socketConnected }"
              >
                {{ socketConnected ? "实时连接" : "正在重连" }}
              </span>
              <span v-if="selectedJob" class="job-badge" :class="selectedJob.status">
                {{ jobStatusLabel(selectedJob.status) }}
              </span>
            </div>
          </div>
          <div v-if="selectedJob" class="job-controls">
            <button
              v-if="isActiveJob(selectedJob) && selectedJob.interactive"
              class="button dark"
              type="button"
              @click="focusAgentBrowser"
            >
              切到浏览器
            </button>
            <button
              v-if="['queued','running'].includes(selectedJob.status)"
              class="button light"
              type="button"
              @click="controlSelectedJob('pause')"
            >
              暂停
            </button>
            <button
              v-if="selectedJob.status === 'paused'"
              class="button dark"
              type="button"
              @click="controlSelectedJob('resume')"
            >
              继续
            </button>
            <button
              v-if="['queued','running','paused'].includes(selectedJob.status)"
              class="button light"
              type="button"
              @click="controlSelectedJob('stop')"
            >
              停止
            </button>
            <button
              v-if="['failed','stopped','completed','incomplete','interrupted'].includes(selectedJob.status)"
              class="button light"
              type="button"
              @click="controlSelectedJob('retry')"
            >
              重试
            </button>
          </div>
          <div
            v-if="selectedJob && !isActiveJob(selectedJob)"
            class="job-result-card"
            :class="selectedJob.status"
          >
            <div>
              <span>任务结果</span>
              <strong>{{ submissionStatusLabel(selectedJob.submission_status) }}</strong>
              <small>
                第 {{ selectedJob.attempt || 1 }} 次 ·
                模式：{{ selectedJob.auto_submit ? "允许最终提交" : "仅填写" }} ·
                {{ selectedJob.interactive ? "可操控浏览器" : "无头浏览器" }}
              </small>
              <small>
                最近阶段：{{ currentAgentState?.message || "无可用执行日志" }} ·
                {{ artifacts.length ? `${artifacts.length} 个执行凭证` : "暂无可核对产物" }}
              </small>
              <small v-if="selectedJob.verified_at">
                人工核对：{{ formatLocalTime(selectedJob.verified_at) }}
                <template v-if="selectedJob.verification_note">
                  · {{ selectedJob.verification_note }}
                </template>
              </small>
            </div>
            <p v-if="selectedJob.error" class="result-error">{{ selectedJob.error }}</p>
            <p v-else-if="selectedJobResult">{{ selectedJobResult }}</p>
            <p v-else>本次任务没有生成文字摘要，请查看执行凭证。</p>
            <div class="result-actions">
              <button class="button light" type="button" @click="openSelectedJobAssignment">
                打开作业核对
              </button>
              <button
                v-if="['filled_pending_review','partial_pending_review'].includes(selectedJob.submission_status)"
                class="button dark"
                type="button"
                :disabled="verificationBusy"
                @click="verifySelectedSubmission('filled_verified')"
              >
                确认已核对
              </button>
              <button
                v-if="['submitted_unverified','submission_unknown'].includes(selectedJob.submission_status)"
                class="button dark"
                type="button"
                :disabled="verificationBusy"
                @click="verifySelectedSubmission('submitted_verified')"
              >
                确认已提交
              </button>
              <button
                v-if="['submitted_unverified','submission_unknown','not_submitted'].includes(selectedJob.submission_status)"
                class="button light"
                type="button"
                :disabled="verificationBusy"
                @click="verifySelectedSubmission('not_submitted')"
              >
                确认未提交
              </button>
            </div>
          </div>
          <div v-if="selectedJob && isActiveJob(selectedJob)" class="progress-track">
            <span
              role="progressbar"
              aria-label="Agent 任务进度"
              aria-valuemin="0"
              aria-valuemax="100"
              :aria-valuenow="selectedJob?.progress || 0"
              :style="{ width: `${selectedJob?.progress || 0}%` }"
            ></span>
          </div>
          <div
            v-if="selectedJob && isActiveJob(selectedJob)"
            class="agent-current-state"
            aria-live="polite"
          >
            <strong>{{ currentAgentState?.message || "等待 Agent 日志" }}</strong>
            <p v-if="actionSummary(currentAgentState?.data?.actions)">
              {{ actionSummary(currentAgentState.data.actions) }}
            </p>
            <p v-if="currentAgentState?.data?.url">{{ currentAgentState.data.url }}</p>
            <small v-if="currentAgentState">
              {{ new Date(currentAgentState.created_at).toLocaleTimeString("zh-CN") }}
            </small>
          </div>
          <div v-if="selectedJob && screenshot" class="agent-live-preview">
            <div class="agent-live-preview-heading">
              <span>Agent 最近页面</span>
              <a :href="screenshot" target="_blank" rel="noreferrer">查看原图</a>
            </div>
            <img :src="screenshot" alt="Agent 最近页面截图">
          </div>
          <div class="timeline">
            <div v-for="event in visibleEvents" :key="event.id" class="timeline-item">
              <span class="event-dot" :class="event.kind"></span>
              <div>
                <strong>{{ event.message || event.kind }}</strong>
                <p v-if="actionSummary(event.data?.actions)">
                  {{ actionSummary(event.data.actions) }}
                </p>
                <p v-if="event.data?.thought">{{ event.data.thought }}</p>
                <small>{{ new Date(event.created_at).toLocaleTimeString("zh-CN") }}</small>
              </div>
            </div>
          </div>
          <div v-if="artifacts.length" class="artifact-list">
            <div class="section-title">
              <span>执行凭证</span>
              <span class="count">{{ artifacts.length }}</span>
            </div>
            <a
              v-for="artifact in artifacts"
              :key="artifact.id"
              :href="`http://127.0.0.1:8877/api/artifacts/${artifact.id}`"
              target="_blank"
              rel="noreferrer"
            >
              {{ artifactLabel(artifact.kind) }}
            </a>
          </div>
        </aside>
      </main>

      <main v-else-if="activePage === 'platforms'" class="platform-layout">
        <aside class="account-rail">
          <div class="section-title">
            <span>平台</span>
            <span class="count">{{ platforms.length }}</span>
          </div>
          <div class="platform-list">
            <button
              v-for="platform in platforms"
              :key="platform.name"
              type="button"
              class="platform-chip active"
            >
              <strong>{{ platform.label }}</strong>
              <small>{{ platform.name }}</small>
            </button>
          </div>
          <button class="button light wide" type="button" @click="accountModalOpen = true">
            添加
          </button>
          <div class="section-title account-title">
            <span>账号</span>
            <span class="count">{{ accounts.length }}</span>
          </div>
          <div v-if="unauthorizedAccount" class="account-onboarding">
            <div>
              <strong>完成账号登录授权</strong>
              <p>
                {{ unauthorizedAccount.last_login_error
                  || "授权后才能采集作业和启动 Agent。" }}
              </p>
            </div>
            <button
              class="button dark"
              type="button"
              @click="authorizeAccount(unauthorizedAccount)"
            >
              开始授权
            </button>
          </div>
          <div
            v-for="account in accounts"
            :key="account.id"
            class="account-item"
            :class="{ active: account.id === selectedAccountId }"
          >
            <button
              class="account-select"
              type="button"
              @click="selectedAccountId = account.id"
            >
              <span class="account-dot" :class="account.status"></span>
              <span>
                <strong>{{ account.name }}</strong>
                <small>
                  {{ platformLabel(account.platform) }} ·
                  {{ accountStatusLabel(account.status) }}
                </small>
              </span>
            </button>
            <div class="account-menu-wrap">
              <button
                class="account-more"
                type="button"
                aria-label="账号操作"
                @click.stop="toggleAccountMenu(account.id)"
              >
                ...
              </button>
              <div v-if="accountMenuId === account.id" class="account-menu">
                <button
                  type="button"
                  :disabled="hasActiveJobForAccount(account.id)"
                  @click.stop="refreshAccountCookie(account)"
                >
                  刷新 Cookie
                </button>
                <button type="button" @click.stop="openRenameAccount(account)">
                  重命名
                </button>
                <button
                  class="danger"
                  type="button"
                  :disabled="hasActiveJobForAccount(account.id)"
                  @click.stop="openDeleteAccount(account)"
                >
                  删除
                </button>
              </div>
            </div>
          </div>
        </aside>

        <section class="assignment-board">
          <div class="board-heading">
            <div>
              <span class="eyebrow">当前账号</span>
              <h2>{{ selectedAccount?.name || "尚未选择账号" }}</h2>
            </div>
            <div class="filters">
              <label>
                <span>学期</span>
                <select v-model="selectedSemester">
                  <option value="">全部学期</option>
                  <option v-for="semester in semesters" :key="semester" :value="semester">
                    {{ semester }}
                  </option>
                </select>
              </label>
              <label class="submit-toggle">
                <input v-model="autoSubmit" type="checkbox">
                <span title="允许 Agent 点击最终提交">允许最终提交</span>
              </label>
              <label class="submit-toggle">
                <input v-model="interactiveBrowser" type="checkbox">
                <span>可操控浏览器</span>
              </label>
              <button
                class="button dark"
                type="button"
                :disabled="collecting || !selectedAccount || Boolean(selectedAccountActiveJob)"
                @click="collectAssignments"
              >
                {{ selectedAccountActiveJob
                  ? "任务运行中"
                  : (collecting ? "采集中..." : "采集") }}
              </button>
            </div>
          </div>
          <div
            v-if="collectionRun"
            class="collection-run-strip"
            :class="collectionRun.status"
          >
            <div class="collection-run-copy">
              <strong>{{ collectionRunLabel(collectionRun) }}</strong>
              <span v-if="collectionRun.status === 'running'">
                {{ collectionRun.processed_courses || 0 }}/{{ collectionRun.total_courses || "?" }}
                个课程
              </span>
              <span v-else-if="collectionRun.finished_at">
                {{ formatLocalTime(collectionRun.finished_at) }}
              </span>
            </div>
            <div
              v-if="collectionRun.status === 'running'"
              class="collection-run-progress"
            >
              <span
                role="progressbar"
                aria-label="采集进度"
                aria-valuemin="0"
                aria-valuemax="100"
                :aria-valuenow="collectionRun.progress || 0"
                :style="{ width: `${collectionRun.progress || 0}%` }"
              ></span>
            </div>
            <details
              v-if="collectionRun.errors?.length"
              class="collection-run-errors"
            >
              <summary>查看失败课程</summary>
              <p
                v-for="item in collectionRun.errors"
                :key="`${item.course_id}-${item.course_name}`"
              >
                {{ item.course_name || item.course_id }}：{{ item.error }}
              </p>
            </details>
          </div>
          <div v-if="filteredAssignments.length" class="selection-toolbar">
            <label>
              <input
                type="checkbox"
                :checked="allFilteredSelected"
                @change="toggleSelectAllAssignments"
              >
              <span>全选</span>
            </label>
            <span>已选择 {{ selectedAssignmentIds.length }} 个</span>
            <button
              class="button dark"
              type="button"
              :disabled="batchCreating || selectedAssignmentIds.length === 0"
              @click="createBatchJobs"
            >
              {{ batchCreating ? "批量创建中..." : "批量启动 Agent" }}
            </button>
          </div>
          <div v-if="groupedAssignments.length === 0" class="empty-state">
            <h3>{{ assignmentEmptyTitle() }}</h3>
            <p>{{ assignmentEmptyDescription() }}</p>
          </div>
          <section
            v-for="group in groupedAssignments"
            :key="`${group.courseName}_${group.semester}`"
            class="course-group"
          >
            <div class="course-heading">
              <h3>{{ group.courseName }}</h3>
              <span>{{ group.semester }}</span>
            </div>
            <article
              v-for="assignment in group.items"
              :key="assignment.id"
              class="assignment-row"
              :class="{ selected: selectedAssignmentIds.includes(assignment.id) }"
            >
              <label class="assignment-select">
                <input
                  type="checkbox"
                  :checked="selectedAssignmentIds.includes(assignment.id)"
                  @change="toggleAssignmentSelection(assignment.id)"
                >
              </label>
              <div class="assignment-copy">
                <strong>{{ assignment.title }}</strong>
                <p v-if="assignment.deadline_text">
                  {{ assignment.deadline_text }}
                  <span
                    class="deadline-state"
                    :class="deadlineState(assignment.deadline_text).className"
                  >
                    {{ deadlineState(assignment.deadline_text).label }}
                  </span>
                </p>
                <p v-else>未显示截止时间</p>
                <span
                  v-if="latestJobForAssignment(assignment.id)"
                  class="assignment-job-state"
                  :class="latestJobForAssignment(assignment.id).status"
                >
                  {{ jobStatusLabel(latestJobForAssignment(assignment.id).status) }}
                  · 第 {{ latestJobForAssignment(assignment.id).attempt || 1 }} 次
                  <template v-if="!isActiveJob(latestJobForAssignment(assignment.id))">
                    · {{ submissionStatusLabel(latestJobForAssignment(assignment.id).submission_status) }}
                  </template>
                </span>
              </div>
              <div class="assignment-actions">
                <button
                  class="assignment-view"
                  type="button"
                  :disabled="openingAssignmentId === assignment.id"
                  @click="openAssignment(assignment)"
                >
                  {{ openingAssignmentId === assignment.id ? "打开中" : "查看" }}
                </button>
                <button
                  class="button light assignment-start"
                  type="button"
                  :disabled="busy"
                  @click="startAssignmentJob(assignment)"
                >
                  {{ assignmentJobLabel(assignment.id) }}
                </button>
              </div>
            </article>
          </section>
        </section>
        <aside class="profile-preview-panel">
          <div class="profile-preview-heading">
            <div>
              <span class="eyebrow">当前 Profile</span>
              <h2>{{ selectedAccount?.name || "浏览器预览" }}</h2>
            </div>
            <div class="preview-actions">
              <button
                class="button light"
                type="button"
                :disabled="Boolean(selectedAccountActiveJob)"
                @click="openInteractiveBrowser"
              >
                {{ selectedAccountActiveJob ? "Agent 正在使用" : "打开可操控浏览器" }}
              </button>
              <button class="button ghost" type="button" @click="refreshProfilePreview(true)">
                刷新
              </button>
            </div>
          </div>
          <div class="profile-preview-url">{{ profilePreviewUrl || "等待浏览器画面" }}</div>
          <div
            class="profile-preview-image"
            :class="{ loading: previewLoading }"
          >
            <img v-if="profilePreview" :src="profilePreview" alt="当前账号浏览器页面">
            <div v-else class="profile-preview-empty">
              {{ previewLoading
                ? "正在加载当前账号页面..."
                : (profilePreviewTitle || "当前账号页面暂不可用") }}
            </div>
          </div>
        </aside>
      </main>

      <main v-else-if="activePage === 'connectors'" class="settings-page">
        <div class="page-heading">
          <span class="eyebrow">消息平台</span>
          <h2>连接器</h2>
          <p>选择一个机器人或消息平台进行配置。</p>
        </div>
        <div class="connector-grid">
          <button
            v-for="connector in connectors"
            :key="connector.id"
            type="button"
            class="connector-card"
            :class="{ available: connector.available }"
            @click="openConnector(connector)"
          >
            <span class="connector-mark">{{ connector.name.slice(0, 1) }}</span>
            <span>
              <strong>{{ connector.name }}</strong>
              <small>{{ connector.detail }}</small>
            </span>
            <span
              v-if="connector.id === 'weixin'"
              class="status-tag"
              :class="{ enabled: settingsInfo?.weixin_configured }"
            >
              {{ settingsInfo?.weixin_ready ? "会话就绪" : settingsInfo?.weixin_configured ? "已绑定" : "配置" }}
            </span>
            <span v-else class="status-tag">即将支持</span>
          </button>
        </div>
      </main>

      <main v-else class="settings-page">
        <div class="page-heading">
          <h2>设置</h2>
          <p>所有数据、日志和执行凭证均保存在本机。</p>
        </div>
        <section class="settings-row">
          <div>
            <strong>模型</strong>
            <p>
              {{ settingsInfo?.provider_name || "custom" }} ·
              {{ (settingsInfo?.models || []).join(" → ") || "未读取" }} ·
              思考 {{ settingsInfo?.reasoning_effort || "high" }}
            </p>
          </div>
          <div class="settings-actions">
            <button class="button light" type="button" @click="openModelModal">配置模型</button>
          </div>
        </section>
        <section class="schedule-settings">
          <div class="settings-row schedule-heading">
            <div>
              <strong>自动检测</strong>
              <p>应用运行期间，按账号间隔采集当前仍可提交的作业。</p>
            </div>
          </div>
          <div v-if="accounts.length === 0" class="schedule-empty">
            添加并授权账号后可以启用自动检测。
          </div>
          <article
            v-for="account in accounts"
            :key="account.id"
            class="schedule-account-row"
          >
            <div class="schedule-account-copy">
              <strong>{{ account.name }}</strong>
              <small>
                {{ platformLabel(account.platform) }} ·
                {{ accountStatusLabel(account.status) }}
              </small>
            </div>
            <label class="schedule-toggle">
              <input
                type="checkbox"
                :checked="Boolean(account.auto_collect_enabled)"
                :disabled="account.status !== 'ready'"
                @change="updateCollectionSchedule(account, {
                  enabled: $event.target.checked
                })"
              >
              <span>启用</span>
            </label>
            <label
              class="schedule-interval"
              :class="{ disabled: !account.auto_collect_enabled || account.status !== 'ready' }"
            >
              <span>间隔</span>
              <select
                :value="account.auto_collect_interval_minutes || 360"
                :disabled="account.status !== 'ready' || !account.auto_collect_enabled"
                @change="updateCollectionSchedule(account, {
                  interval_minutes: Number($event.target.value)
                })"
              >
                <option :value="30">30 分钟</option>
                <option :value="60">1 小时</option>
                <option :value="180">3 小时</option>
                <option :value="360">6 小时</option>
                <option :value="720">12 小时</option>
                <option :value="1440">24 小时</option>
              </select>
            </label>
            <div
              v-if="account.auto_collect_enabled || account.last_collection_at"
              class="schedule-result"
              :class="account.last_collection_status"
            >
              <strong>{{ collectionStatusLabel(account) }}</strong>
              <small v-if="nextCollectionLabel(account)">
                {{ nextCollectionLabel(account) }}
              </small>
              <small v-else-if="account.last_collection_at">
                {{ formatLocalTime(account.last_collection_at) }}
              </small>
              <small
                v-if="account.last_collection_error"
                class="schedule-error"
              >
                {{ account.last_collection_error }}
              </small>
            </div>
          </article>
        </section>
        <section class="settings-row">
          <div>
            <strong>数据目录</strong>
            <p class="path-text">{{ settingsInfo?.data_root || "未读取" }}</p>
          </div>
          <button class="button light" type="button" @click="openDataDirectory">打开</button>
        </section>
        <section class="settings-row">
          <div>
            <strong>日志目录</strong>
            <p class="path-text">{{ settingsInfo?.logs_root || "未读取" }}</p>
          </div>
          <button class="button light" type="button" @click="openLogDirectory">
            打开
          </button>
        </section>
      </main>
    </div>

    <div
      v-if="launchConfirmOpen"
      class="modal-backdrop"
      @mousedown.self="launchConfirmOpen = false"
    >
      <div
        class="modal-card modal-narrow"
        role="dialog"
        aria-modal="true"
        aria-labelledby="launch-confirm-title"
        tabindex="-1"
      >
        <div class="modal-heading">
          <div>
            <span class="eyebrow">高风险操作</span>
            <h2 id="launch-confirm-title">确认允许最终提交</h2>
          </div>
          <button class="modal-close" type="button" @click="launchConfirmOpen = false">
            关闭
          </button>
        </div>
        <p class="modal-message">
          即将启动 {{ pendingLaunch.assignments.length }} 个 Agent 任务，并允许 Agent 点击最终提交。
          任务结束后系统只会标记为“允许提交，结果未验证”，仍需回到作业页面人工确认。
        </p>
        <div class="confirm-job-list">
          <span
            v-for="assignment in pendingLaunch.assignments.slice(0, 4)"
            :key="assignment.id"
          >
            {{ assignment.title }}
          </span>
          <span v-if="pendingLaunch.assignments.length > 4">
            另有 {{ pendingLaunch.assignments.length - 4 }} 个作业
          </span>
        </div>
        <div class="modal-actions">
          <button class="button light" type="button" @click="launchConfirmOpen = false">
            取消
          </button>
          <button class="button danger-button" type="button" @click="confirmAutoSubmitLaunch">
            确认启动并允许提交
          </button>
        </div>
      </div>
    </div>

    <div
      v-if="accountModalOpen"
      class="modal-backdrop"
      @mousedown.self="accountModalOpen = false"
    >
      <div
        class="modal-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="account-modal-title"
        tabindex="-1"
      >
        <div class="modal-heading">
          <div>
            <span class="eyebrow">平台账号</span>
            <h2 id="account-modal-title">添加平台账号</h2>
          </div>
          <button class="modal-close" type="button" @click="accountModalOpen = false">关闭</button>
        </div>
        <label class="field-label">
          <span>名称</span>
          <input
            id="account-name-input"
            v-model="accountForm.name"
            placeholder="例如：我的学习通"
          >
        </label>
        <label class="field-label">
          <span>平台</span>
          <select v-model="accountForm.platform">
            <option v-for="platform in platforms" :key="platform.name" :value="platform.name">
              {{ platform.label }}
            </option>
          </select>
        </label>
        <div class="modal-actions">
          <button class="button light" type="button" @click="accountModalOpen = false">取消</button>
          <button class="button dark" type="button" :disabled="busy" @click="createAccount">
            添加
          </button>
        </div>
      </div>
    </div>

    <div
      v-if="weixinModalOpen"
      class="modal-backdrop"
      @mousedown.self="weixinModalOpen = false"
    >
      <div
        class="modal-card modal-narrow"
        role="dialog"
        aria-modal="true"
        aria-labelledby="weixin-modal-title"
        tabindex="-1"
      >
        <div class="modal-heading">
          <div>
            <span class="eyebrow">iLink Bot</span>
            <h2 id="weixin-modal-title">绑定微信机器人</h2>
          </div>
          <button class="modal-close" type="button" @click="weixinModalOpen = false">关闭</button>
        </div>
        <div class="qr-box">
          <img v-if="weixinQr" :src="weixinQr" alt="微信机器人绑定二维码">
          <span v-else>正在获取二维码...</span>
        </div>
        <p class="modal-message">{{ weixinMessage }}</p>
        <div v-if="weixinStatus === 'need_verifycode'" class="inline-form">
          <input v-model="weixinVerifyCode" inputmode="numeric" placeholder="手机验证码">
          <button class="button dark" type="button" @click="pollWeixinStatus">提交</button>
        </div>
        <div v-if="weixinStatus === 'confirmed'" class="modal-actions">
          <button class="button light" type="button" @click="refreshWeixinSession">刷新会话</button>
          <button class="button dark" type="button" @click="weixinModalOpen = false">完成</button>
        </div>
      </div>
    </div>

    <div
      v-if="modelModalOpen"
      class="modal-backdrop"
      @mousedown.self="modelModalOpen = false"
    >
      <div
        class="modal-card modal-wide"
        role="dialog"
        aria-modal="true"
        aria-labelledby="model-modal-title"
        tabindex="-1"
      >
        <div class="modal-heading">
          <div>
            <span class="eyebrow">AI Provider</span>
            <h2 id="model-modal-title">模型配置</h2>
          </div>
          <button class="modal-close" type="button" @click="modelModalOpen = false">关闭</button>
        </div>
        <div class="model-grid">
          <label class="field-label">
            <span>供应商名称</span>
            <input
              id="model-provider-input"
              v-model="modelForm.provider_name"
              placeholder="ssh"
            >
          </label>
          <label class="field-label">
            <span>API 地址</span>
            <input v-model="modelForm.base_url" placeholder="https://sshzyu.com/v1">
          </label>
        </div>
        <label class="field-label">
          <span>API 密钥</span>
          <input v-model="modelForm.api_key" type="password" placeholder="留空则保留原密钥">
        </label>
        <label class="field-label">
          <span>思考强度</span>
          <select v-model="modelForm.reasoning_effort">
            <option value="none">无</option>
            <option value="minimal">最少</option>
            <option value="low">低</option>
            <option value="medium">中</option>
            <option value="high">高</option>
            <option value="xhigh">极高</option>
          </select>
        </label>
        <div class="model-priority">
          <div class="section-title">
            <span>模型优先级</span>
            <span class="count">{{ modelForm.models.length }}</span>
          </div>
          <div v-for="(model, index) in modelForm.models" :key="model" class="model-row">
            <span class="model-rank">{{ index === 0 ? "主模型" : `备用 ${index}` }}</span>
            <strong>{{ model }}</strong>
            <button class="text-button danger" type="button" @click="removeModel(index)">删除</button>
          </div>
          <div class="inline-form">
            <input
              v-model="newModelName"
              placeholder="输入或选择模型名"
              @keyup.enter="addModel(newModelName)"
            >
            <button class="button light" type="button" @click="addModel(newModelName)">添加</button>
          </div>
          <button class="button light wide" type="button" @click="fetchModelList">从服务商拉取模型列表</button>
          <select
            v-if="modelCatalog.length"
            class="model-catalog"
            @change="addModel($event.target.value)"
          >
            <option value="">从模型列表添加</option>
            <option v-for="model in modelCatalog" :key="model" :value="model">{{ model }}</option>
          </select>
        </div>
        <p class="modal-message">{{ modelMessage }}</p>
        <div class="modal-actions split">
          <button class="button light" type="button" @click="testModelConnection">测试连接</button>
          <button class="button dark" type="button" @click="saveModelSettings">保存配置</button>
        </div>
      </div>
    </div>

    <div
      v-if="renameModalOpen"
      class="modal-backdrop"
      @mousedown.self="renameModalOpen = false"
    >
      <div
        class="modal-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="rename-modal-title"
        tabindex="-1"
      >
        <div class="modal-heading">
          <div>
            <span class="eyebrow">账号名称</span>
            <h2 id="rename-modal-title">重命名账号</h2>
          </div>
          <button class="modal-close" type="button" @click="renameModalOpen = false">关闭</button>
        </div>
        <label class="field-label">
          <span>名称</span>
          <input
            id="rename-account-input"
            v-model="renameValue"
            @keyup.enter="saveRenameAccount"
          >
        </label>
        <div class="modal-actions">
          <button class="button light" type="button" @click="renameModalOpen = false">取消</button>
          <button class="button dark" type="button" @click="saveRenameAccount">保存</button>
        </div>
      </div>
    </div>

    <div
      v-if="deleteModalOpen"
      class="modal-backdrop"
      @mousedown.self="deleteModalOpen = false"
    >
      <div
        class="modal-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="delete-modal-title"
        tabindex="-1"
      >
        <div class="modal-heading">
          <div>
            <span class="eyebrow">删除账号</span>
            <h2 id="delete-modal-title">确认删除 {{ deleteAccountName }}</h2>
          </div>
          <button class="modal-close" type="button" @click="deleteModalOpen = false">关闭</button>
        </div>
        <p class="modal-message">
          将同时删除该账号的本地 Cookie、浏览器 Profile、作业记录和 Agent 任务。
        </p>
        <div class="modal-actions">
          <button class="button light" type="button" @click="deleteModalOpen = false">取消</button>
          <button class="button danger-button" type="button" @click="confirmDeleteAccount">
            确认删除
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
