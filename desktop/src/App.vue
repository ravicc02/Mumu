<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import QRCode from "qrcode";
import StartupExperience from "./components/StartupExperience.vue";
import { onboardingComplete, completeOnboarding } from "./lib/onboarding";
import {
  Check, CheckCheck, Circle, CircleAlert, ClipboardList, ClipboardCheck,
  Ellipsis, ExternalLink, FileText, Layers3,
  LayoutDashboard, LoaderCircle, LogIn, Monitor, BookOpen,
  Pause, Play, PlugZap, Plus, RefreshCw, RotateCcw, Save,
  Settings2, Square, X, CircleHelp,
  Search, ArrowUpRight, ChevronLeft, ChevronRight, ArrowDownUp,
  Maximize2, Minimize2, PanelRightClose, PanelRightOpen
} from "@lucide/vue";
import { api } from "./api";
import { buildWorkflowStages, canJobAction, mergeJobState, reconcileEvents } from "./lib/job-state";
import PlatformLogo from "./components/PlatformLogo.vue";
import ScreenshotViewer from "./components/ScreenshotViewer.vue";
import LiveBrowser from "./components/LiveBrowser.vue";
import { conciseMessage, presentEvent, taskIssue } from "./lib/task-presentation";
import LearningWorkspace from "./components/LearningWorkspace.vue";
import AttendanceWorkspace from "./components/AttendanceWorkspace.vue";
import PlatformWorkspace from "./components/PlatformWorkspace.vue";
import SettingsDialog from "./components/SettingsDialog.vue";
import LocalProfileAvatar from "./components/LocalProfileAvatar.vue";
import { loadLocalProfile, saveLocalProfile } from "./lib/local-profile";
import { allowedProjectLink, checkPublishedRelease, loadUpdatePreference, saveUpdatePreference } from "./lib/app-info";
import TaskMonitor from "./components/TaskMonitor.vue";
import TaskTown from "./components/TaskTown.vue";
import EducoderCourses from "./components/EducoderCourses.vue";
import AccountAvatar from "./components/AccountAvatar.vue";
import AccountAuthorization from "./components/AccountAuthorization.vue";
import LearningTaskDetails from "./components/LearningTaskDetails.vue";
import { createAccountAuthorization } from "./lib/account-authorization";
import { assignmentBrowserTask, activeAssignmentBrowserTask } from "./lib/task-browser";
import { assignmentReadiness, requiresMaterials } from "./lib/assignment-readiness";
import { finalScreenshotSections } from "./lib/screenshots";
import { createCollectionController } from "./lib/collection-controller";
import { collectAccountBatch } from "./lib/collection-batch";
import { assignmentDeadline, assignmentExpired } from "./lib/assignment-availability";
import { buildTaskOverview } from "./lib/task-overview";
import { accountsForBusiness, businessAccountId, supportsBusiness } from "./lib/platforms";
import LeiTabs from "./components/leibox/LeiTabs.vue";
import LeiResizeHandle from "./components/leibox/LeiResizeHandle.vue";
const desktopAvailable = Boolean(window.desktopApi);
const isolatedPreview = Boolean(import.meta.env?.VITE_API_BASE);

function preference(key, fallback) {
  try { return JSON.parse(localStorage.getItem(`studio:${key}`)) ?? fallback; }
  catch { return fallback; }
}
function remember(key, value) {
  try { localStorage.setItem(`studio:${key}`, JSON.stringify(value)); }
  catch { /* Storage can be unavailable in private browser sessions. */ }
}

const navItems = [
  { id: "dashboard", label: "面板", icon: LayoutDashboard },
  { id: "platforms", label: "平台", icon: Layers3 },
  { id: "assignments", label: "作业", icon: ClipboardList },
  { id: "learning", label: "网课", icon: BookOpen },
  { id: "attendance", label: "签到", icon: ClipboardCheck },
  { id: "connectors", label: "连接器", icon: PlugZap }
];
const connectors = [
  { id: "weixin", name: "微信机器人", available: true }
];

const activePage = ref("dashboard");
const taskMonitor = ref(null);
const townMonitor = ref(null);
const selectedPlatformId = ref("chaoxing");
const availabilityNow = ref(Date.now());
const collectionBatchBusy = ref(false);
const collectionBatchRows = ref([]);
const assignmentQuery = ref("");
const assignmentFilter = ref("all");
const assignmentSort = ref("course");
const listWidth = ref(Math.max(300, Math.min(520, Number(preference("list-width", 370)) || 370)));
const logPanelOpen = ref(preference("log-open", true));
const browserExpanded = ref(false);
const captureMode = ref("task");
const batchFailures = ref([]);
let semesterAccountId = "";
const accounts = ref([]);
const assignments = ref([]);
const semesters = ref([]);
const jobs = ref([]);
const learningRuns = ref([]);
const taskSnapshotAt = ref(0);
let jobsRefreshPromise = null;
const learningCourseId = ref("");
const events = ref([]);
const artifacts = ref([]);
const accountCollections = ref({});
const health = ref(null);
const settingsInfo = ref(null);
const settingsSection = ref("account");
const settingsOpen = ref(false);
const localProfile = ref(loadLocalProfile());
const autoCheckUpdates = ref(loadUpdatePreference());
const updateState = ref({status:"idle",message:""});
let executionDefaultsLoaded = false;
const platforms = ref([]);
const selectedAccountId = ref(preference("account", ""));
const collectionController = createCollectionController({
  fetchRuns: accountId => api.collectionRuns(accountId, 10),
  changed: states => { accountCollections.value = states; }
});
const collectionRun = computed(() => accountCollections.value[selectedAccountId.value]?.run || null);
const collecting = computed(() => Boolean(accountCollections.value[selectedAccountId.value]?.pending || collectionRun.value?.status === "running"));
const selectedSemester = ref("");
const selectedJobId = ref("");
const assignmentViewer = ref(null);
const detailViewer = ref(null);
const taskDetailsOpen = ref(false);
const learningResultOpen = ref(false);
const learningResultId = ref("");
const taskDetailsLoading = ref(false);
const deleteTaskTarget = ref(null);
const deleteTaskBusy = ref(false);
const deleteTaskError = ref("");
const deletedTaskIds = ref([]);
const jobActionPending = ref({});
const loginAccountId = ref("");
const authorizationOpen = ref(false);
const authorizationState = ref({ status: "idle", platform: "chaoxing", accountId: "", account: null, message: "", error: "" });

const notice = ref("");
const busy = ref(false);
const error = ref("");
const screenshot = ref("");
const profilePreview = ref("");
const profilePreviewUrl = ref("");
const profilePreviewTitle = ref("");
const previewLoading = ref(false);
const autoSubmit = ref(false);
const interactiveBrowser = true;
const selectedAssignmentIds = ref([]);
const batchCreating = ref(false);
const openingAssignmentId = ref("");
const inspectingAssignmentId = ref("");
const selectableAssignments = computed(() => filteredAssignments.value.filter(item => !requiresMaterials(item)));
const socketConnected = ref(false);
const launchConfirmOpen = ref(false);
const pendingLaunch = ref({ kind: "", assignments: [] });


const accountMenuId = ref("");



const deleteModalOpen = ref(false);
const deleteAccountId = ref("");
const deleteAccountName = ref("");
const weixinModalOpen = ref(false);
const weixinQr = ref("");
const weixinCode = ref("");
const weixinStatus = ref("idle");
const weixinMessage = ref("");
const weixinVerifyCode = ref("");
const weixinMode = ref("loading");
const weixinConfig = ref(null);
const weixinPreferencesBusy = ref(false);
const weixinSessionBusy = ref(false);
const verificationBusy = ref(false);
const setupOpen = ref(false);
const setupDismissible = ref(false);
function reopenSetup() { settingsOpen.value = false; setupDismissible.value = true; setupOpen.value = true; }
const setupScope = computed(() => settingsInfo.value?.data_root || health.value?.data_root || "");
let initializationChecked = false;
async function initializeFirstRun() {
  if (initializationChecked) return;
  if (window.desktopApi?.bootstrapState) {
    try { if ((await window.desktopApi.bootstrapState()).status !== "ready") { setupOpen.value = true; return; } }
    catch { setupOpen.value = true; return; }
  }
  initializationChecked = true;
  if (health.value?.ok && setupScope.value && accounts.value.some(account => account.status === "ready")) {
    try { if (!onboardingComplete(setupScope.value)) completeOnboarding(setupScope.value, { model: health.value.ai_configured ? "configured" : "skipped", platform: "authorized" }); }
    catch (failure) { notice.value = failure.message; }
    setupOpen.value = false;
    return;
  }
  setupOpen.value = !onboardingComplete(setupScope.value);
}
async function setupRuntimeReady() { await connectBackend(); if (!setupDismissible.value) await initializeFirstRun(); }
async function finishSetup() { setupOpen.value = false; await connectBackend(); }
let socket = null;
let socketReconnectTimer = null;
let socketGeneration = 0;
const authorizationFlow = createAccountAuthorization(api, {
  changed: state => {
    authorizationState.value = state;
    loginAccountId.value = ["opening", "waiting", "verifying"].includes(state.status) ? state.accountId : "";
  },
  completed: (account, records, message) => {
    accounts.value = records;
    selectedAccountId.value = account.id;
    notice.value = message;
  },
});
let weixinTimer = null;
let profilePreviewTimer = null;
let agentSnapshotTimer = null;
let agentSnapshotBusy = false;
let weixinGeneration = 0;
let weixinPollingGeneration = null;
let connectionTimer = null;
let connecting = null;
let appUnmounted = false;
let taskDetailsTrigger = null;
let noticeTimer = null;
let browserExpansionAnimation = null;

const activeJobStatuses = new Set(["queued", "running", "paused", "stopping"]);
const attentionJobStatuses = new Set(["needs_intervention"]);
const busyJobStatuses = new Set([
  ...activeJobStatuses
]);
const snapshotJobStatuses = new Set(["paused", "incomplete", ...attentionJobStatuses]);

function canSnapshotActiveBrowser(job) {
  return Boolean(
    job
    && job.interactive
    && job.browser_available
    && snapshotJobStatuses.has(job.status)
  );
}

const selectedAccount = computed(() => (
  accounts.value.find((account) => account.id === selectedAccountId.value) || null
));
const assignmentAccounts = computed(() => accountsForBusiness(accounts.value, "assignments"));
const learningAccounts = computed(() => accountsForBusiness(accounts.value, "learning"));

function ensureBusinessAccount() {
  const business = activePage.value;
  if (!["assignments", "learning"].includes(business)) return;
  const next = businessAccountId(accounts.value, business, selectedAccountId.value, preference("account:" + business, ""));
  if (next !== selectedAccountId.value) selectedAccountId.value = next;
}

const filteredAssignments = computed(() => {
  const query = assignmentQuery.value.trim().toLocaleLowerCase();
  return assignments.value.filter((assignment) => {
    const job = latestJobForAssignment(assignment.id);
    return !assignmentExpired(assignment, new Date(availabilityNow.value))
      && (!selectedSemester.value || assignment.semester === selectedSemester.value)
      && (!query || `${assignment.title} ${assignment.course_name}`.toLocaleLowerCase().includes(query))
      && (assignmentFilter.value === "all"
        || (assignmentFilter.value === "new" && !job)
        || (assignmentFilter.value === "attention" && (requiresMaterials(assignment) || (job && ["needs_intervention", "incomplete", "interrupted", "failed"].includes(job.status)))));
  }).sort((left, right) => assignmentSort.value === "title"
    ? left.title.localeCompare(right.title, "zh-CN")
    : left.course_name.localeCompare(right.course_name, "zh-CN"));
});

const allFilteredSelected = computed(() => (
  selectableAssignments.value.length > 0
  && selectableAssignments.value.every((assignment) => (
    selectedAssignmentIds.value.includes(assignment.id)
  ))
));

const selectedJob = computed(() => (
  jobs.value.find((job) => job.id === selectedJobId.value) || null
));

const recentAssignmentJobs = computed(() => buildTaskOverview(jobs.value, learningRuns.value));
const batchCollectionById = computed(() => Object.fromEntries(collectionBatchRows.value.map(row => [row.id,row])));
const batchCollectionDone = computed(() => collectionBatchRows.value.filter(row => !["queued","running"].includes(row.status)).length);


const assignmentFilterItems = computed(() => [
  { value: "all", label: "全部作业" },
  { value: "new", label: "未执行" },
  { value: "attention", label: "待处理" }
]);
const selectedCount = computed(() => selectedAssignmentIds.value.length);
const browserShowsCapture = computed(() => Boolean(platformLiveJob.value && (selectedAccountActiveJob.value || captureMode.value === "task")));

function accountPlatform(accountId) {
  return accounts.value.find(account => account.id === accountId)?.platform || "chaoxing";
}

const selectedJobAttempts = computed(() => {
  if (!selectedJob.value) return [];
  return jobs.value.filter((job) => (
    job.account_id === selectedJob.value.account_id
    && job.assignment_id === selectedJob.value.assignment_id
  )).sort((left, right) => (
    new Date(right.created_at).getTime() - new Date(left.created_at).getTime()
  ));
});

const platformLiveJob = computed(() => assignmentBrowserTask(jobs.value, selectedAccountId.value, selectedJobId.value));
const selectedAccountActiveJob = computed(() => activeAssignmentBrowserTask(platformLiveJob.value));
const accountTaskChoices = computed(() => recentAssignmentJobs.value.filter(job => job.task_kind === "assignment" && job.account_id === selectedAccountId.value));

const selectedJobIssue = computed(() => taskIssue(selectedJob.value, selectedJobResult.value));
const detailBrowserTab = ref("live");

const screenshotSections = computed(() => finalScreenshotSections(events.value, artifacts.value));

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

const visibleEvents = computed(() => {
  let stepNumber = 0;
  return events.value
    .filter((event) => (
      !["heartbeat", "screenshot", "screenshot_segment"].includes(event.kind)
    ))
    .map((event) => {
      if (event.kind === "step") stepNumber += 1;
      return {
        ...presentEvent(event),
        step_number: event.kind === "step" ? stepNumber : null,
        state_label: logEventStateLabel(event.kind)
      };
    })
    .reverse();
});

const currentAgentState = computed(() => presentEvent(
  [...events.value]
    .reverse()
    .find((event) => !["heartbeat", "screenshot", "notification", "notification_error"].includes(event.kind))
  || null
));

const structuredFillCount = computed(() => (
  Math.max(Number(selectedJob.value?.completed_units || 0), ...events.value
    .filter(event => ["quiz_filled", "quiz_unit_filled"].includes(event.kind))
    .map(event => Number(event.data?.filled_count || 0)))
));

const completedStepCount = computed(() => (
  Math.max(
    events.value.filter((event) => event.kind === "step").length,
    structuredFillCount.value
  )
));

const progressUnitLabel = computed(() => (
  events.value.some((event) => event.kind === "quiz_extracted")
    ? "作答单元"
    : "动作步骤"
));

const workflowStages = computed(() => buildWorkflowStages(selectedJob.value, events.value, {
  completedCount: completedStepCount.value, unitLabel: progressUnitLabel.value
}));

const activeJobs = computed(() => (
  jobs.value.filter((job) => busyJobStatuses.has(job.status) || job.browser_available)
));
const activeJob = computed(() => activeJobs.value.find(job => ['running', 'paused', 'stopping'].includes(job.status))
  || activeJobs.value.find(job => job.browser_available) || activeJobs.value[0] || null);

const selectedJobResult = computed(() => {
  const resultEvent = [...events.value]
    .reverse()
    .find((event) => (
      ["result_summary", "completed", "incomplete", "stopped"].includes(event.kind)
    ));
  return conciseMessage(resultEvent?.message);
});

const jobStatusLabels = {
  queued: "排队中",
  running: "运行中",
  paused: "已暂停",
  stopping: "停止中",
  stopped: "已停止",
  completed: "已完成",
  incomplete: "未完成",
  needs_intervention: "待介入",
  failed: "中断",
  interrupted: "中断"
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
  return platforms.value.find(item => item.name === platform)?.label || platformLabels[platform] || platform || "未知平台";
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
  return "采集完成 · 当前 " + assignments.value.filter(item => !assignmentExpired(item, new Date(availabilityNow.value))).length + " 个待处理";
}

function assignmentEmptyTitle() {
  if (!selectedAccount.value) return "尚未选择账号";
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
    return "本轮未发现待处理作业";
  }
  return "暂无待处理作业";
}

function assignmentEmptyDescription() {
  if (selectedAccount.value?.last_login_error) {
    return selectedAccount.value.last_login_error;
  }
  if (collectionRun.value?.status === "failed") {
    return collectionRun.value.error || "请检查登录状态和网络后重试。";
  }
  if (collectionRun.value?.status === "partial") {
    return `有 ${collectionRun.value.failed_courses} 个课程采集失败，请展开上方失败详情。`;
  }
  return "";
}

function latestJobForAssignment(assignmentId) {
  return jobs.value.find((job) => job.assignment_id === assignmentId) || null;
}

function queuePosition(job) {
  if (job?.queue_position) return job.queue_position;
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
  if (!job) return "执行";
  if (job.browser_available || ["needs_intervention", "completed"].includes(job.status) || !canJobAction(job, "retry")) return "查看任务";
  return "重试";
}

function isActiveJob(job) {
  return Boolean(job && activeJobStatuses.has(job.status));
}

function isNeedsIntervention(job) {
  return Boolean(job && attentionJobStatuses.has(job.status));
}

function hasActiveJobForAccount(accountId) {
  return jobs.value.some((job) => (
    job.account_id === accountId && (busyJobStatuses.has(job.status) || job.browser_available)
  ));
}

function accountOperationBusy(accountId) {
  return hasActiveJobForAccount(accountId)
    || Boolean(accounts.value.find(account => account.id === accountId)?.learning_run)
    || Boolean(accountCollections.value[accountId]?.pending || accountCollections.value[accountId]?.run?.status === "running");
}

function artifactLabel(kind) {
  return {
    screenshot: "步骤截图",
    final_screenshot: "最终截图",
    live_screenshot: "最近实时画面",
    result_json: "运行结果 JSON"
  }[kind] || kind;
}

function artifactTitle(artifact) {
  const section = screenshotSections.value.find(item => item.id === artifact.id);
  return section ? `截图第 ${section.index}/${section.count} 段` : artifactLabel(artifact.kind);
}

function shortText(value, limit = 42) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  return text.length > limit ? `${text.slice(0, limit)}…` : text;
}

function actionSummary(actions, details = []) {
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
  if (Array.isArray(details) && details.length > 0) {
    return details.map((item) => {
      const type = item?.type || "";
      const data = item?.data || {};
      const nested = Object.values(data).find((value) => (
        value && typeof value === "object" && !Array.isArray(value)
      )) || data;
      const label = labels[type] || type || "动作";
      if (type.includes("Navigate") && nested.url) return "打开任务页面";
      if (type.includes("Input") && nested.text) {
        return `输入「${shortText(nested.text)}」`;
      }
      if (type.includes("SendKeys") && nested.keys) {
        return `按键 ${shortText(nested.keys, 24)}`;
      }
      if (type.includes("Click") && nested.index !== undefined) {
        return `点击元素 #${nested.index}`;
      }
      if (type.includes("Scroll") && nested.amount !== undefined) {
        return `滚动 ${nested.amount}`;
      }
      if (type.includes("Upload") && nested.path) {
        return `上传 ${shortText(nested.path, 48)}`;
      }
      if (type.includes("Done")) {
        return "结束任务";
      }
      const fallback = shortText(JSON.stringify(nested));
      return label;
    }).join("；");
  }
  if (!Array.isArray(actions) || actions.length === 0) return "";
  return actions.map((action) => labels[action] || action).join("、");
}

function eventMessagePreview(message, limit = 140) {
  const text = String(message || "");
  return text.length > limit ? `${text.slice(0, limit)}…` : text;
}

function logEventStateLabel(kind) {
  return {
    queued: "排队",
    browser_starting: "启动浏览器",
    session_ready: "登录就绪",
    browser: "浏览器",
    agent_ready: "Agent 就绪",
    thinking: "分析中",
    step: "已完成",
    screenshot: "页面画面",
    paused: "已暂停",
    resumed: "已继续",
    stopping: "停止中",
    stopped: "已停止",
    completed: "已完成",
    incomplete: "未完成",
    needs_intervention: "待介入",
    failed: "中断",
    error: "异常",
    stalled: "疑似卡住",
    interrupted: "中断",
    notification: "通知",
    submission_verified: "已核验",
    submission_reviewed: "已人工核对",
    result_summary: "执行结论",
    diagnostic: "问题诊断",
    quiz_navigating: "读取题型",
    quiz_extracted: "已识别",
    quiz_answers_ready: "答案就绪",
    quiz_filled: "已填写",
    draft_saving: "暂存中",
    draft_saved: "已暂存",
    screenshot_segment: "分段截图",
    quiz_unit_filled: "已填写",
    browser_closed: "浏览器已关闭"
  }[kind] || "状态";
}

function deadlineState(assignment) {
  const now = new Date(availabilityNow.value);
  const deadline = assignmentDeadline(assignment, now);
  if (!deadline) return { label: "截止时间未知", className: "unknown" };
  const remaining = deadline.getTime() - now.getTime();
  if (remaining < 0) return { label: "已过截止", className: "overdue" };
  return remaining < 48 * 3600000 ? { label: "即将截止", className: "soon" } : { label: "作答期内", className: "open" };
}

const connectionLabel = computed(() => {
  if (!health.value) return "后端离线";
  return health.value.ai_configured ? `${health.value.model} 已配置` : "AI 未配置";
});

function setError(value) {
  error.value = conciseMessage(value instanceof Error ? value.message : String(value || ""));
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

async function loadExecutionDefaults() {
  if (executionDefaultsLoaded) return;
  const preferences = await api.preferences();
  autoSubmit.value = preferences.assignments.auto_submit;
  executionDefaultsLoaded = true;
}

async function loadAccounts() {
  accounts.value = await api.accounts();
  if (["assignments", "learning"].includes(activePage.value)) ensureBusinessAccount();
  else if (!accounts.value.some(account => account.id === selectedAccountId.value)) {
    selectedAccountId.value = accounts.value[0]?.id || "";
  }
}

async function refreshProfilePreview(reload = false) {
  if (previewLoading.value) return;
  if (!selectedAccountId.value) {
    profilePreview.value = "";
    profilePreviewUrl.value = "";
    profilePreviewTitle.value = "";
    previewLoading.value = false;
    return;
  }
  previewLoading.value = true;
  const accountId = selectedAccountId.value;
  if (!profilePreview.value) {
    profilePreviewTitle.value = "正在加载当前账号页面...";
  }
  try {
    const preview = await api.preview(accountId, reload);
    if (accountId !== selectedAccountId.value) return;
    profilePreview.value = preview.image;
    profilePreviewUrl.value = preview.url;
    profilePreviewTitle.value = preview.title;
  } catch (previewError) {
    if (accountId !== selectedAccountId.value) return;
    profilePreview.value = "";
    profilePreviewUrl.value = "";
    profilePreviewTitle.value = previewError.message;
  } finally {
    if (accountId === selectedAccountId.value) previewLoading.value = false;
  }
}

function startProfilePreview() {
  clearInterval(profilePreviewTimer);
  if (
    activePage.value !== "assignments"
    || !selectedAccountId.value
    || collecting.value
    || selectedAccountActiveJob.value
    || (captureMode.value === "task" && platformLiveJob.value)
  ) {
    return;
  }
  refreshProfilePreview(true);
  profilePreviewTimer = setInterval(() => refreshProfilePreview(false), 2500);
}

async function syncPlatformLiveJob() {
  if (taskDetailsOpen.value || activePage.value !== "assignments" || !platformLiveJob.value) {
    return;
  }
  if (selectedJobId.value !== platformLiveJob.value.id || (!events.value.length && !taskDetailsLoading.value)) {
    await selectJob(platformLiveJob.value.id);
  }
}

function openConnector(connector) {
  if (connector.id === "weixin") {
    openWeixinConnector();
    return;
  }
  notice.value = `${connector.name} 连接器尚未开放。`;
}

async function loadAssignments() {
  if (!supportsBusiness(selectedAccount.value, "assignments")) { assignments.value = []; return; }
  const accountId = selectedAccountId.value, semester = selectedSemester.value;
  const result = await api.assignments({ accountId: accountId || undefined, submittable: true });
  if (accountId !== selectedAccountId.value || semester !== selectedSemester.value) return;
  assignments.value = result.filter(item => !assignmentExpired(item) && (!semester || item.semester === semester));
  const ids = new Set(assignments.value.map(item => item.id));
  selectedAssignmentIds.value = selectedAssignmentIds.value.filter(id => ids.has(id));
}

async function loadCollectionRuns(accountId = selectedAccountId.value) {
  await collectionController.refresh(accountId);
}

async function loadSemesters() {
  if (!supportsBusiness(selectedAccount.value, "assignments")) {
    semesters.value = []; selectedSemester.value = ""; return;
  }
  const accountId = selectedAccountId.value;
  const result = await api.semesters(accountId || undefined);
  if (accountId !== selectedAccountId.value) return;
  semesters.value = result;
  if (semesterAccountId !== accountId) {
    const saved = preference(`semester:${accountId}`, null);
    selectedSemester.value = saved === "" || result.includes(saved) ? saved : (result[0] || "");
    semesterAccountId = accountId;
    return;
  }
  if (!selectedSemester.value && semesters.value.length > 0) {
    selectedSemester.value = semesters.value[0];
  }
  if (selectedSemester.value && !semesters.value.includes(selectedSemester.value)) {
    selectedSemester.value = semesters.value[0] || "";
  }
}

async function loadJobs() {
  if (jobsRefreshPromise) return jobsRefreshPromise;
  jobsRefreshPromise = (async () => {
    const [incoming, courses] = await Promise.all([api.jobs(), api.learningRuns()]);
    if (appUnmounted) return;
    const removed = new Set(deletedTaskIds.value);
    jobs.value = incoming.filter(job => !removed.has(job.id)).map(job => mergeJobState(jobs.value.find(previous => previous.id === job.id), job));
    learningRuns.value = courses.filter(run => !removed.has(run.id)).map(run => mergeJobState(learningRuns.value.find(previous => previous.id === run.id), run));
    taskSnapshotAt.value = Date.now();
    if (!selectedJobId.value && jobs.value.length > 0) await selectJob(jobs.value[0].id);
  })().finally(() => { jobsRefreshPromise = null; });
  return jobsRefreshPromise;
}

async function refreshWorkspace() {
  await Promise.all([
    loadAccounts(),
    loadJobs(),
    loadSettings(),
    loadPlatforms(),
    loadExecutionDefaults()
  ]);
  if (selectedAccountId.value) {
    await Promise.all([
      loadSemesters(),
      loadAssignments(),
      loadCollectionRuns()
    ]);
  }
}

function connectBackend() {
  if (connecting) return connecting;
  connecting = (async () => {
    for (let attempt = 0; attempt < 20; attempt += 1) {
      await loadHealth();
      if (health.value) {
        await refreshWorkspace();
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
  })().finally(() => { connecting = null; });
  return connecting;
}

async function confirmPlatformAuthorization(account) {
  try { await api.confirmAuthorization(account.id); notice.value = "正在保存浏览器授权"; }
  catch (failure) { setError(failure); }
}

async function startLogin(accountId = selectedAccountId.value) {
  authorizationOpen.value = true;
  if (authorizationFlow.pending()) return;
  const account = accounts.value.find(item => item.id === accountId);
  if (account) await authorizationFlow.start(account.platform, account);
}

async function authorizeAccount(account) {
  accountMenuId.value = "";
  await startLogin(account.id);
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
    if (activePage.value === "assignments") await refreshProfilePreview(true);
  } catch (refreshError) {
    setError(refreshError);
  } finally {
    busy.value = false;
  }
}

async function toggleAccountMenu(accountId) {
  accountMenuId.value = accountMenuId.value === accountId ? "" : accountId;
  if (accountMenuId.value) {
    await nextTick();
    document.querySelector(".account-menu button:not(:disabled)")?.focus();
  }
}

function navigateAccountMenu(event) {
  if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  const items = [...event.currentTarget.querySelectorAll("button:not(:disabled)")];
  const current = items.indexOf(document.activeElement);
  const next = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1
    : (current + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
  items[next]?.focus();
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
    if (settingsOpen.value) { settingsOpen.value = false; return; }
    if (learningResultOpen.value) { learningResultOpen.value = false; return; }
    if (authorizationOpen.value) { authorizationOpen.value = false; return; }
    if (deleteTaskTarget.value) { if (!deleteTaskBusy.value) deleteTaskTarget.value = null; return; }
    if (browserExpanded.value) { toggleBrowserExpansion(); return; }
    if (taskDetailsOpen.value) {
      taskDetailsOpen.value = false;
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


    if (launchConfirmOpen.value) {
      launchConfirmOpen.value = false;
      return;
    }
    const openMenuId = accountMenuId.value;
    accountMenuId.value = "";
    document.querySelector(`.account-item[data-account-id="${openMenuId}"] .account-more`)?.focus();
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

function collectionSkipReason(account) {
  if (account.status !== "ready") return "账号尚未授权";
  if (!platforms.value.find(item => item.name === account.platform)?.assignments) return "该平台不支持作业采集";
  if (account.learning_run) return "网课任务占用中";
  if (jobs.value.some(job => job.account_id === account.id && (job.browser_available || activeJobStatuses.has(job.status)))) return "作业任务占用中";
  if (accountCollections.value[account.id]?.pending || accountCollections.value[account.id]?.run?.status === "running") return "该账号正在采集";
  return "";
}

async function collectOneAccount(account, academicYear) {
  if (!collectionController.begin(account.id)) throw new Error("该账号正在采集");
  let finishedRun = null;
  try {
    const result = await api.collect(account.id, academicYear);
    finishedRun = result.finished;
    const current = await api.assignments({ accountId: account.id, submittable: true });
    const count = current.filter(item => !assignmentExpired(item)).length;
    await loadAccounts();
    if (selectedAccountId.value === account.id) { await loadSemesters(); await loadAssignments(); }
    return { ...result, submittable: count };
  } finally {
    collectionController.finish(account.id, finishedRun);
    await loadCollectionRuns(account.id).catch(() => {});
  }
}

async function collectAssignments() {
  const account = selectedAccount.value;
  if (!account) return;
  const reason = collectionSkipReason(account);
  if (reason) { notice.value = reason; return; }
  error.value = "";
  try {
    const result = await collectOneAccount(account, currentAcademicYear());
    notice.value = account.name + "：采集完成，当前 " + result.submittable + " 个待处理" + (result.status === "partial" ? "，部分课程失败" : "");
  } catch (failure) { setError(new Error(account.name + "：" + failure.message)); }
}

async function collectAllAccounts() {
  if (collectionBatchBusy.value) return;
  collectionBatchBusy.value = true; collectionBatchRows.value = []; error.value = "";
  try {
    const preferences = await api.preferences();
    const result = await collectAccountBatch([...assignmentAccounts.value], { concurrency: preferences.system.collection_concurrency,
      skip: collectionSkipReason, collect: account => collectOneAccount(account, currentAcademicYear()),
      changed: rows => { if (!appUnmounted) collectionBatchRows.value = rows; } });
    if (!appUnmounted) notice.value = "一键采集结束：" + result.filter(row => ["success", "partial"].includes(row.status)).length + " 个已采集，" + result.filter(row => row.status === "error").length + " 个失败，" + result.filter(row => row.status === "skipped").length + " 个跳过";
  } catch (failure) { if (!appUnmounted) setError(failure); }
  finally { collectionBatchBusy.value = false; }
}

async function createJob(assignment) {
  busy.value = true;
  error.value = "";
  notice.value = `正在创建任务：${assignment.title}`;
  try {
    if (assignmentExpired(assignment)) throw new Error("作业已超过截止时间，请重新采集确认。");
    const job = await api.createJob(
      assignment.id,
      autoSubmit.value,
      interactiveBrowser
    );
    jobs.value = [job, ...jobs.value];
    if (selectedAccountId.value === job.account_id) {
      if (job.blocked_by_job_id) await openTaskDetails(job.id);
      else await selectJob(job.id);
    }
    notice.value = `Agent 任务已创建：${assignment.title}`;
  } catch (jobError) {
    setError(jobError);
  } finally {
    busy.value = false;
  }
}

async function inspectAssignment(assignment) {
  inspectingAssignmentId.value = assignment.id;
  try {
    const checked = await api.inspectAssignment(assignment.id);
    assignments.value = assignments.value.map(item => item.id === checked.id ? checked : item);
    notice.value = "作业要求与作答状态已更新";
  } catch (inspectError) { setError(inspectError); }
  finally { inspectingAssignmentId.value = ""; }
}

async function startAssignmentJob(assignment) {
  if (requiresMaterials(assignment)) { await openAssignment(assignment); return; }
  const existingJob = latestJobForAssignment(assignment.id);
  if (existingJob && assignmentJobLabel(assignment.id) === "查看任务") {
    await openTaskDetails(existingJob.id);
    notice.value = "已打开该作业的任务状态。";
    return;
  }
  if (existingJob) {
    await requestRetry(existingJob);
    return;
  }
  if (autoSubmit.value && assignment.platform !== 'educoder') {
    pendingLaunch.value = { kind: "single", assignments: [assignment] };
    launchConfirmOpen.value = true;
    return;
  }
  await createJob(assignment);
}

async function openAssignment(assignment) {
  const existing = jobs.value.find(job => job.assignment_id === assignment.id && job.browser_available);
  if (existing) {
    await selectJob(existing.id);
    await focusAgentBrowser(existing.id);
    return;
  }
  openingAssignmentId.value = assignment.id;
  error.value = "";
  notice.value = `正在打开：${assignment.title}`;
  try {
    const result = await api.openAssignment(assignment.id);
    captureMode.value = "profile";
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
  const visibleIds = new Set(selectableAssignments.value.map(assignment => assignment.id));
  if (allFilteredSelected.value) {
    selectedAssignmentIds.value = selectedAssignmentIds.value.filter(id => !visibleIds.has(id));
    return
  }
  selectedAssignmentIds.value = [...new Set([...selectedAssignmentIds.value, ...visibleIds])];
}

async function createBatchJobs() {
  const candidates = assignments.value.filter((assignment) => (
    selectedAssignmentIds.value.includes(assignment.id)
  ));
  if (candidates.length === 0) {
    notice.value = "请先选择至少一个作业。"
    return
  }
  const selected = candidates.filter((assignment) => (
    !assignmentExpired(assignment)
    && !requiresMaterials(assignment)
    && !isActiveJob(latestJobForAssignment(assignment.id))
    && !latestJobForAssignment(assignment.id)?.browser_available
    && !["needs_intervention", "completed"].includes(latestJobForAssignment(assignment.id)?.status)
    && (!latestJobForAssignment(assignment.id) || canJobAction(latestJobForAssignment(assignment.id), "retry"))
  ));
  const skipped = candidates.length - selected.length;
  if (selected.length === 0) {
    notice.value = skipped > 0
      ? `所选 ${skipped} 个作业需要先查看任务状态，请从作业行进入对应任务。`
      : "请先选择至少一个作业。";
    return;
  }
  if (autoSubmit.value && selected.some(assignment => assignment.platform !== 'educoder')) {
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
  const batchAccountId = selected[0]?.account_id || selectedAccountId.value;
  const submit = autoSubmit.value;
  const interactive = interactiveBrowser;
  batchFailures.value = [];
  try {
    for (let index = 0; index < selected.length; index += 1) {
      const assignment = selected[index]
      notice.value = `正在创建批量任务 ${index + 1}/${selected.length}：${assignment.title}`
      try {
        if (assignmentExpired(assignment)) throw new Error("作业已超过截止时间，请重新采集确认。");
        const existing = latestJobForAssignment(assignment.id);
        const job = existing
          ? await api.retryJob(existing.id, submit)
          : await api.createJob(assignment.id, submit, interactive);
        createdJobs.push(job)
      } catch (batchError) {
        failures.push({ id: assignment.id, title: assignment.title, error: batchError.message });
      }
    }
    jobs.value = [...createdJobs, ...jobs.value]
    if (selectedAccountId.value === batchAccountId) {
      batchFailures.value = failures;
      selectedAssignmentIds.value = failures.map(item => item.id);
    }
    if (createdJobs.length > 0 && selectedAccountId.value === batchAccountId) {
      await selectJob(createdJobs[0].id)
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
  if (pending.kind === "retry") {
    await controlSelectedJob("retry", { jobId: pending.jobId, autoSubmit: true });
    return;
  }
  if (pending.kind === "single" && pending.assignments[0]) {
    await createJob(pending.assignments[0]);
    return;
  }
  if (pending.kind === "batch") {
    await executeBatchJobs(pending.assignments);
  }
}

function jobPending(job = selectedJob.value) {
  return Boolean(job && jobActionPending.value[job.id]);
}

function applyJob(job) {
  const previous = jobs.value.find(item => item.id === job.id);
  const merged = mergeJobState(previous, job);
  jobs.value = [merged, ...jobs.value.filter(item => item.id !== job.id)]
    .sort((left, right) => new Date(right.created_at) - new Date(left.created_at));
}

async function requestRetry(job) {
  if (!job || jobPending(job)) return;
  if (autoSubmit.value && job.platform !== 'educoder') {
    pendingLaunch.value = { kind: "retry", jobId: job.id, assignments: [{ id: job.assignment_id, title: job.assignment_title }] };
    launchConfirmOpen.value = true;
    return;
  }
  await controlSelectedJob("retry", { jobId: job.id, autoSubmit: false });
}

async function controlSelectedJob(action, options = {}) {
  const jobId = options.jobId || selectedJobId.value;
  if (!jobId || jobActionPending.value[jobId]) return;
  jobActionPending.value[jobId] = action;
  notice.value = { pause: "正在暂停当前步骤...", resume: "正在恢复任务...", stop: "正在停止并释放浏览器...", retry: "正在关闭旧会话并创建重试任务...", continue: "正在接回人工处理后的浏览器..." }[action];
  try {
    const job = action === "retry"
      ? await api.retryJob(jobId, Boolean(options.autoSubmit))
      : await ({ pause: api.pauseJob, resume: api.resumeJob, stop: api.stopJob, continue: api.continueJob }[action])(jobId);
    applyJob(job);
    if (action === "retry") {
      if (selectedJobId.value === jobId) await selectJob(job.id);
      notice.value = `已创建第 ${job.attempt} 次任务：${job.platform === 'educoder' ? (job.auto_submit ? '自动评测与修复' : '仅保存代码') : (job.auto_submit ? '允许最终提交' : '仅填写，待审核')}`;
    } else {
      notice.value = { pause: "已暂停，当前步骤结束后等待恢复", resume: "任务已恢复", stop: "正在停止任务，日志会返回退出结果", continue: "已接回原浏览器，继续处理剩余内容" }[action];
    }
  } catch (controlError) {
    setError(controlError);
    await loadJobs().catch(() => {});
  } finally {
    delete jobActionPending.value[jobId];
  }
}

async function verifySelectedSubmission(status) {
  const jobId = selectedJobId.value;
  if (!jobId || jobActionPending.value[jobId]) return;
  jobActionPending.value[jobId] = "review";
  verificationBusy.value = true;
  try {
    const job = await api.verifySubmission(jobId, status);
    applyJob(job);
    const [eventData, artifactData] = await Promise.all([api.events(job.id), api.artifacts(job.id)]);
    if (selectedJobId.value === jobId) {
      events.value = reconcileEvents(eventData);
      artifacts.value = artifactData;
    }
    await loadJobs();
    notice.value = status === "submitted_verified"
      ? "已确认提交成功，任务已完成，账号队列可以继续"
      : status === "filled_verified" ? "填写内容已审核，尚未确认提交。提交后可点击“我已提交”。" : "核对结果已更新，可以继续执行或重试";
  } catch (verificationError) { setError(verificationError); }
  finally { verificationBusy.value = false; delete jobActionPending.value[jobId]; }
}


async function focusAgentBrowser(jobId = selectedJobId.value) {
  if (!jobId) return;
  const job = jobs.value.find(item => item.id === jobId);
  if (job?.platform === "educoder") {
    if (taskDetailsOpen.value) { detailBrowserTab.value = "live"; await nextTick(); await detailViewer.value?.expand(); return; }
    selectedAccountId.value = job.account_id;
    activePage.value = "assignments";
    await nextTick();
    await selectJob(jobId); await nextTick();
    await assignmentViewer.value?.expand(); return;
  }
  try {
    await api.focusJobBrowser(jobId);
    notice.value = "已打开实时浏览器；手动操作会同步到当前页面预览";
  } catch (focusError) {
    setError(focusError);
  }
}

function startAgentSnapshot() {
  clearInterval(agentSnapshotTimer);
  agentSnapshotTimer = null;
}

async function finishSelectedIntervention(jobId = selectedJobId.value) {
  if (!jobId || jobActionPending.value[jobId]) return;
  jobActionPending.value[jobId] = "close_browser";
  notice.value = "正在关闭当前任务浏览器...";
  try {
    const job = await api.finishIntervention(jobId);
    applyJob(job);
    notice.value = "浏览器已关闭，任务状态和审核记录已保留";
    const [eventData, artifactData] = await Promise.all([
      api.events(job.id),
      api.artifacts(job.id)
    ]);
    if (selectedJobId.value === jobId) {
      events.value = reconcileEvents(eventData);
      artifacts.value = artifactData;
    }
    await loadJobs();
  } catch (interventionError) {
    setError(interventionError);
  } finally {
    delete jobActionPending.value[jobId];
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

async function openWorkspaceBrowser() {
  if (selectedAccountActiveJob.value?.browser_available) return focusAgentBrowser(selectedAccountActiveJob.value.id);
  if (browserShowsCapture.value && platformLiveJob.value) {
    return openAssignment({ id: platformLiveJob.value.assignment_id, title: platformLiveJob.value.assignment_title });
  }
  await openInteractiveBrowser();
  captureMode.value = "profile";
}

async function toggleBrowserExpansion() {
  const panel = document.querySelector('.platform-browser-panel');
  const from = panel?.getBoundingClientRect();
  browserExpansionAnimation?.cancel();
  browserExpanded.value = !browserExpanded.value;
  await nextTick();
  if (!panel || !from || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const to = panel.getBoundingClientRect();
  if (!to.width || !to.height) return;
  browserExpansionAnimation = panel.animate([
    { transformOrigin: 'top left', transform: `translate(${from.left - to.left}px,${from.top - to.top}px) scale(${from.width / to.width},${from.height / to.height})`, opacity: .88 },
    { transformOrigin: 'top left', transform: 'translate(0,0) scale(1,1)', opacity: 1 }
  ], { duration: 460, easing: 'cubic-bezier(.16,1,.3,1)' });
  await browserExpansionAnimation.finished.catch(() => {});
}

async function switchAccount(accountId) {
  if (selectedAccountId.value === accountId) return;
  if (taskDetailsOpen.value && jobPending()) {
    notice.value = "当前任务操作尚未返回，请稍后切换账号";
    return;
  }
  selectedAccountId.value = accountId;
  await nextTick();
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const avatar = document.querySelector('.account-item.active .account-avatar');
  const identity = document.querySelector('.board-account-name');
  avatar?.animate([{ transform: 'scale(.8) rotate(-7deg)' }, { transform: 'scale(1.12) rotate(3deg)', offset: .6 }, { transform: 'none' }], { duration: 420, easing: 'cubic-bezier(.22,1,.36,1)' });
  identity?.animate([{ opacity: 0, transform: 'translateX(-16px)' }, { opacity: 1, transform: 'none' }], { duration: 320, easing: 'cubic-bezier(.16,1,.3,1)' });
}

async function openSelectedJobAssignment() {
  const job = selectedJob.value;
  if (!job) return;
  try {
    await api.openAssignment(job.assignment_id);
    notice.value = `已在 ${job.account_name} 的浏览器中打开作业，请核对实际提交状态`;
    if (selectedAccountId.value === job.account_id) {
      captureMode.value = "profile";
      await refreshProfilePreview(false);
    }
  } catch (openError) { setError(openError); }
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
  const afterId = Math.max(0, ...events.value.map(event => Number(event.id || 0)));
  const currentSocket = new WebSocket(api.wsUrl(jobId, afterId));
  socket = currentSocket;
  currentSocket.onopen = async () => {
    if (generation !== socketGeneration) {
      currentSocket.close();
      return;
    }
    socketConnected.value = true;
    try {
      const [job, artifactData] = await Promise.all([api.job(jobId), api.artifacts(jobId)]);
      if (generation !== socketGeneration || selectedJobId.value !== jobId) return;
      applyJob(job);
      artifacts.value = artifactData;
    } catch {
      if (generation === socketGeneration) currentSocket.close();
    }
  };
  currentSocket.onmessage = async (message) => {
    if (generation !== socketGeneration) return;
    try {
      const event = JSON.parse(message.data);
      if (event.kind === "resync_required") {
        await selectJob(jobId);
        return;
      }
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
        "queued", "status", "paused", "resumed", "stopping", "stopped",
        "completed", "incomplete", "needs_intervention", "error", "interrupted",
        "submission_verified", "submission_reviewed", "browser_closed"
      ].includes(event.kind)) {
        const updatedJob = await api.job(jobId);
        applyJob(updatedJob);
      }
      if ([
        "stopped", "completed", "incomplete", "needs_intervention", "error", "interrupted", "final_screenshot"
      ].includes(event.kind)) {
        const result = await api.artifacts(jobId);
        if (generation === socketGeneration && selectedJobId.value === jobId) artifacts.value = result;
      }
    } catch {
      if (generation === socketGeneration) currentSocket.close();
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
  taskDetailsLoading.value = true;
  events.value = [];
  artifacts.value = [];
  screenshot.value = "";
  closeJobSocket();
  try {
    const [eventData, artifactData] = await Promise.all([
      api.events(jobId),
      api.artifacts(jobId)
    ]);
    if (selectedJobId.value !== jobId) return;
    events.value = reconcileEvents(eventData);
    artifacts.value = artifactData;
    const latestImage = [...eventData].reverse().find((event) => event.data?.image);
    if (latestImage) screenshot.value = latestImage.data.image;
    if (!screenshot.value) {
      const latestScreenshot = [...artifactData].reverse().find((artifact) => (
        ["screenshot", "final_screenshot", "live_screenshot"].includes(artifact.kind)
      ));
      if (latestScreenshot) {
        screenshot.value = api.artifactUrl(latestScreenshot.id);
      }
    }
  } catch (loadError) {
    if (selectedJobId.value === jobId) setError(loadError);
  } finally {
    if (selectedJobId.value === jobId) taskDetailsLoading.value = false;
  }

  if (selectedJobId.value === jobId) connectJobSocket(jobId);
}

async function openTaskDetails(jobId) {
  if (!taskDetailsOpen.value) taskDetailsTrigger = document.activeElement;
  taskDetailsOpen.value = true;
  await selectJob(jobId);
}

async function modelSettingsSaved() {
  await Promise.all([loadHealth(), loadSettings()]);
  notice.value = "模型配置已保存";
}

async function preferencesSaved({ preferences, section }) {
  if (section === "assignments") {
    autoSubmit.value = preferences.assignments.auto_submit;
  }
  try { await loadAccounts(); } catch (loadError) { setError(loadError); }
}

async function openWeixinConnector() {
  const generation = ++weixinGeneration;
  clearInterval(weixinTimer);
  weixinTimer = null;
  weixinModalOpen.value = true;
  weixinMode.value = "loading";
  weixinMessage.value = "";
  weixinQr.value = "";
  try {
    const config = await api.weixinConnector();
    if (generation !== weixinGeneration) return;
    weixinConfig.value = config;
    if (config.configured) weixinMode.value = "settings";
    else await openWeixinBinding();
  } catch (configError) {
    if (generation !== weixinGeneration) return;
    weixinMode.value = "error";
    weixinMessage.value = configError.message;
  }
}

async function updateWeixinPreference(key, enabled) {
  if (weixinPreferencesBusy.value || weixinSessionBusy.value || !weixinConfig.value) return;
  const previous = { ...weixinConfig.value.preferences };
  weixinConfig.value = { ...weixinConfig.value, preferences: { ...previous, [key]: enabled } };
  weixinPreferencesBusy.value = true;
  weixinMessage.value = "";
  try {
    const preferences = await api.updateWeixinPreferences({ [key]: enabled });
    weixinConfig.value = { ...weixinConfig.value, preferences };
    notice.value = "微信推送设置已保存";
  } catch (preferenceError) {
    weixinConfig.value = { ...weixinConfig.value, preferences: previous };
    weixinMessage.value = preferenceError.message;
  } finally {
    weixinPreferencesBusy.value = false;
  }
}

function openCollectionSettings() {
  weixinModalOpen.value = false;
  openSettings("assignments");
}

async function openWeixinBinding() {
  const generation = ++weixinGeneration;
  weixinModalOpen.value = true;
  weixinMode.value = "binding";
  weixinQr.value = "";
  weixinStatus.value = "loading";
  weixinMessage.value = "正在获取二维码...";
  clearInterval(weixinTimer);
  try {
    const result = await api.weixinQr();
    if (generation !== weixinGeneration) return;
    weixinCode.value = result.code;
    const image = await QRCode.toDataURL(result.qr_text, {
      margin: 2,
      width: 280
    });
    if (generation !== weixinGeneration) return;
    weixinQr.value = image;
    weixinStatus.value = "wait";
    weixinMessage.value = "请使用微信扫码并确认授权";
    weixinTimer = setInterval(pollWeixinStatus, 2000);
  } catch (qrError) {
    if (generation !== weixinGeneration) return;
    weixinStatus.value = "error";
    weixinMessage.value = qrError.message;
  }
}

async function pollWeixinStatus() {
  if (!weixinCode.value) return;
  const generation = weixinGeneration;
  if (weixinPollingGeneration === generation) return;
  weixinPollingGeneration = generation;
  try {
    const result = await api.weixinStatus(
      weixinCode.value,
      weixinVerifyCode.value
    );
    if (generation !== weixinGeneration) return;
    weixinStatus.value = result.status;
    if (result.status === "scaned") {
      weixinMessage.value = "已扫码，等待手机确认";
    } else if (result.status === "need_verifycode") {
      weixinMessage.value = "请输入手机显示的验证码";
    } else if (result.status === "confirmed") {
      clearInterval(weixinTimer);
      weixinTimer = null;
      const config = await api.weixinConnector();
      if (generation !== weixinGeneration) return;
      weixinConfig.value = config;
      weixinMode.value = "settings";
      weixinQr.value = "";
      weixinMessage.value = "";
      await loadSettings();
    }
  } catch (statusError) {
    if (generation !== weixinGeneration) return;
    weixinMessage.value = statusError.message;
  } finally {
    if (weixinPollingGeneration === generation) weixinPollingGeneration = null;
  }
}

async function refreshWeixinSession() {
  if (weixinSessionBusy.value || weixinPreferencesBusy.value) return;
  weixinSessionBusy.value = true;
  try {
    const result = await api.weixinRefresh();
    weixinMessage.value = result.message;
    weixinConfig.value = await api.weixinConnector();
    await loadSettings();
  } catch (refreshError) {
    weixinMessage.value = refreshError.message;
  } finally {
    weixinSessionBusy.value = false;
  }
}

async function openDataDirectory() {
  if (window.desktopApi?.openDataDirectory) {
    await window.desktopApi.openDataDirectory();
    return true;
  } else if (settingsInfo.value?.data_root) {
    return await copyValue(settingsInfo.value.data_root, "数据目录路径已复制");
  }
  return false;
}

async function openLogDirectory() {
  if (window.desktopApi?.openLogDirectory) {
    await window.desktopApi.openLogDirectory();
  } else {
    await api.openLogDirectory();
  }
  return true;
}

function openSettings(section = "account") { settingsSection.value = section; settingsOpen.value = true; }
function updateLocalProfile(value) { const saved = saveLocalProfile(value); localProfile.value = saved; return saved; }
function updateAutoCheck(enabled) { saveUpdatePreference(enabled); autoCheckUpdates.value = Boolean(enabled); if(enabled) void checkForUpdates(); }
async function checkForUpdates() {
  if(updateState.value.status === "checking") return;
  updateState.value = {status:"checking",message:""};
  try { const result = await checkPublishedRelease(); if(!appUnmounted) updateState.value = result; }
  catch(error) { if(!appUnmounted) updateState.value = {status:"error",checkedAt:new Date().toISOString(),message:["AbortError","TimeoutError"].includes(error.name)?"更新检查超时，请重试。":error instanceof TypeError?"无法连接版本服务，请稍后重试。":error instanceof SyntaxError?"发布信息无法解析，请到项目主页查看。":error.message}; }
}
async function openProjectLink(url) {
  if(!allowedProjectLink(url)) throw new Error("项目链接无效。");
  if(window.desktopApi) {
    if(!window.desktopApi.openExternal) throw new Error("请重启桌面应用后打开项目链接。");
    await window.desktopApi.openExternal(url);
  } else { window.open(url,"_blank","noopener,noreferrer"); }
}

function goToPage(pageId) {
  accountMenuId.value = "";
  activePage.value = pageId;
}

function addPlatformAccount(platform) {
  authorizeNewAccount(platform);
}

async function authorizeNewAccount(platform) {
  authorizationOpen.value = true;
  if (!authorizationFlow.pending()) await authorizationFlow.start(platform);
}

function goToAssignments(accountId) {
  if (accountId && !supportsBusiness(accounts.value.find(account => account.id === accountId), "assignments")) {
    notice.value = "该平台尚未接入作业处理"; return;
  }
  accountMenuId.value = "";
  if (accountId) selectedAccountId.value = accountId;
  taskDetailsOpen.value = false;
  activePage.value = "assignments";
}

function goToLearning(accountId) {
  if (accountId && !supportsBusiness(accounts.value.find(account => account.id === accountId), "learning")) {
    notice.value = "该平台尚未接入网课"; return false;
  }
  accountMenuId.value = "";
  learningCourseId.value = "";
  selectedAccountId.value = accountId;
  activePage.value = "learning";
  return true;
}

function openOverviewTask(task) {
  if (task.task_kind !== "learning") { openTaskDetails(task.id); return; }
  openLearningResult(task.id);
}

function openLearningResult(runId) {
  learningResultId.value = runId;
  learningResultOpen.value = true;
}

function openLearningWorkspace(task) {
  if (!goToLearning(task.account_id)) return;
  learningResultOpen.value = false;
  learningCourseId.value = task.course_id;
}

function requestDeleteTask(task) {
  deleteTaskError.value = "";
  deleteTaskTarget.value = task;
  nextTick(() => document.querySelector("#cancel-delete-task")?.focus());
}

async function confirmDeleteTask() {
  const task = deleteTaskTarget.value;
  if (!task || deleteTaskBusy.value) return;
  deleteTaskBusy.value = true;
  deleteTaskError.value = "";
  try {
    const result = await api.deleteTask(task);
    const removed = new Set(result.deleted_ids);
    deletedTaskIds.value = [...new Set([...deletedTaskIds.value, ...removed])];
    jobs.value = jobs.value.filter(job => !removed.has(job.id));
    learningRuns.value = learningRuns.value.filter(run => !removed.has(run.id));
    if (task.task_kind !== "learning" && removed.has(selectedJobId.value)) {
      taskDetailsOpen.value = false;
      closeJobSocket();
      selectedJobId.value = ""; events.value = []; artifacts.value = []; screenshot.value = "";
    }
    deleteTaskTarget.value = null;
    notice.value = "任务已删除";
    await loadJobs();
  } catch (failure) { deleteTaskError.value = failure.message; }
  finally { deleteTaskBusy.value = false; }
}

async function refreshTaskOverview() { await Promise.all([loadAccounts(), loadJobs()]); }

async function copyValue(value, message = "已复制") {
  try { await navigator.clipboard.writeText(value); notice.value = message; return true; }
  catch { setError("无法访问剪贴板，请从页面选择并复制文本。"); return false; }
}

function goToAccount(accountId) {
  taskDetailsOpen.value = false;
  selectedAccountId.value = accountId;
  selectedPlatformId.value = accounts.value.find(account => account.id === accountId)?.platform || "chaoxing";
  activePage.value = "platforms";
}


watch(logPanelOpen, (value) => remember("log-open", value));
watch(captureMode, () => startProfilePreview());
watch(selectedJobId, () => { detailBrowserTab.value = "live"; });
watch(collecting, () => startProfilePreview());
watch(weixinModalOpen, open => {
  if (!open) {
    weixinGeneration += 1;
    clearInterval(weixinTimer);
    weixinTimer = null;
  }
});

watch([activePage, accounts], ensureBusinessAccount, { flush: "sync" });

watch(selectedAccountId, async () => {
  if (!selectedAccountId.value) return;
  selectedAssignmentIds.value = [];
  remember("account", selectedAccountId.value);
  if (["assignments", "learning"].includes(activePage.value)) remember("account:" + activePage.value, selectedAccountId.value);
  assignmentQuery.value = "";
  assignmentFilter.value = "all";
  profilePreview.value = "";
  profilePreviewUrl.value = "";
  previewLoading.value = false;
  screenshot.value = "";
  events.value = [];
  artifacts.value = [];
  selectedJobId.value = "";
  taskDetailsLoading.value = false;
  closeJobSocket();
  batchFailures.value = [];
  await loadSemesters();
  await Promise.all([
    loadAssignments(),
    loadCollectionRuns()
  ]);
  await syncPlatformLiveJob();
  startProfilePreview();
});

watch(selectedSemester, () => {
  selectedAssignmentIds.value = [];
  if (selectedAccountId.value) remember(`semester:${selectedAccountId.value}`, selectedSemester.value);
  loadAssignments();
});

watch(activePage, async () => {
  if (activePage.value !== "assignments") browserExpanded.value = false;
  await syncPlatformLiveJob();
  startProfilePreview();
  startAgentSnapshot();
});

watch(selectedAccountActiveJob, async (job) => {
  if (activePage.value !== "assignments") return;
  if (job) {
    clearInterval(profilePreviewTimer);
    profilePreviewTimer = null;
    startAgentSnapshot();
    await syncPlatformLiveJob();
    return;
  }
  clearInterval(agentSnapshotTimer);
  agentSnapshotTimer = null;
  startProfilePreview();
});

watch(taskDetailsOpen, async (isOpen) => {
  await nextTick();
  if (isOpen) {
    document.querySelector("#task-details-close")?.focus();
  } else {
    if (taskDetailsTrigger?.isConnected) taskDetailsTrigger.focus();
    await syncPlatformLiveJob();
  }
});

watch(deleteTaskTarget, async (task, previous) => {
  if (task || !previous) return;
  await nextTick();
  (document.getElementById(`task-actions-${previous.id}`) || document.querySelector(".job-more-button") || document.querySelector(".dashboard-heading button"))?.focus();
});

watch(notice, (message) => {
  clearTimeout(noticeTimer);
  if (message) noticeTimer = setTimeout(() => { notice.value = ""; }, 5000);
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
  if(autoCheckUpdates.value) void checkForUpdates();
  document.addEventListener("click", closeAccountMenuOnOutsideClick);
  document.addEventListener("keydown", closeAccountMenuOnEscape);
  if (window.desktopApi?.bootstrapState) {
    try { setupOpen.value = (await window.desktopApi.bootstrapState()).status !== "ready"; }
    catch { setupOpen.value = true; }
  }
  await connectBackend();
  await initializeFirstRun();
  connectionTimer = setInterval(async () => {
    availabilityNow.value = Date.now();
    const wasOffline = !health.value;
    await loadHealth({ silent: true });
    if (wasOffline && health.value) {
      await connectBackend();
    } else if (health.value) {
      try {
        await Promise.all([loadJobs(), loadAccounts()]);
        if (activePage.value === "assignments" && selectedAccountId.value) {
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
  authorizationFlow.dispose();
  document.removeEventListener("click", closeAccountMenuOnOutsideClick);
  document.removeEventListener("keydown", closeAccountMenuOnEscape);
  closeJobSocket();
  if (weixinTimer) clearInterval(weixinTimer);
  if (profilePreviewTimer) clearInterval(profilePreviewTimer);
  if (agentSnapshotTimer) clearInterval(agentSnapshotTimer);
  collectionController.dispose();
  if (connectionTimer) clearInterval(connectionTimer);
  if (noticeTimer) clearTimeout(noticeTimer);
  browserExpansionAnimation?.cancel();
});
</script>

<template>
  <div class="app-shell" :inert="setupOpen || undefined">
    <header class="topbar">
      <div class="brand">
        <span class="brand-mark"><img src="/brand/mumu.png" width="35" height="35" alt="" /></span>
        <h1>沐沐</h1>
      </div>
      <div class="topbar-breadcrumb"><span>{{ isolatedPreview ? '隔离验证' : '工作区' }}</span><ChevronRight :size="13" /><strong>{{ navItems.find(item => item.id === activePage)?.label }}</strong></div>
      <div class="topbar-meta">
        <TaskMonitor ref="taskMonitor" :hide-trigger="activePage === 'dashboard'" @summary="townMonitor = $event" :tasks="recentAssignmentJobs" :removed-ids="deletedTaskIds" :accounts="accounts" :ready="taskSnapshotAt > 0 && Boolean(settingsInfo?.data_root)" :online="Boolean(health?.ok)" :scope="settingsInfo?.data_root || ''" :updated-at="taskSnapshotAt" :creating="batchCreating" :refresh="loadJobs" @open-task="openOverviewTask" @open-dashboard="activePage = 'dashboard'" />
      </div>
    </header>

    <div v-if="error" class="error-strip" role="alert">
      <span>{{ error }}</span>
      <button class="icon-button" type="button" aria-label="关闭错误提示" title="关闭" @click="error = ''"><X :size="16" /></button>
    </div>

    <div class="app-body">
      <nav class="side-nav" aria-label="主导航">
        <span class="nav-motion-track" :style="{ transform: `translateY(${navItems.findIndex(item => item.id === activePage) * 70}px)` }" aria-hidden="true"></span>
        <button
          v-for="item in navItems"
          :key="item.id"
          type="button"
          class="nav-item"
          :class="{ active: activePage === item.id }"
          :aria-current="activePage === item.id ? 'page' : undefined"
          :title="item.label"
          @click="goToPage(item.id)"
        >
          <component :is="item.icon" :size="18" :stroke-width="1.75" aria-hidden="true" />
          <span>{{ item.label }}</span>
        </button>
        <div class="side-nav-footer">
          <button class="icon-button sidebar-about" type="button" title="帮助与关于" aria-label="帮助与关于" @click="openSettings('about')"><CircleHelp :size="19" :stroke-width="1.7" /></button>
          <button class="sidebar-profile" :class="{active:settingsOpen}" type="button" :title="localProfile.nickname + ' · 设置'" aria-label="打开设置" :aria-expanded="settingsOpen" aria-controls="application-settings-dialog" @click="openSettings('account')"><LocalProfileAvatar :profile="localProfile" :size="36" /></button>
        </div>
      </nav>

      <div class="app-feedback">
        <button
          v-if="activeJob"
          class="global-job-strip"
          type="button"
          :class="activeJob.status"
          @click="openTaskDetails(activeJob.id)"
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
        <Transition name="notice">
          <div v-if="notice" class="notice-line" role="status" aria-live="polite">
            <Check :size="15" aria-hidden="true" />
            <span>{{ notice }}</span>
            <button class="icon-button" type="button" aria-label="关闭通知" title="关闭" @click="notice = ''"><X :size="14" /></button>
          </div>
        </Transition>
      </div>

      <Transition name="page" mode="out-in">
      <main v-if="activePage === 'dashboard'" key="dashboard" class="dashboard-layout">
<section class="dashboard-main town-dashboard">
          <div class="page-heading dashboard-heading">
            <div><h2>任务公司 <span class="heading-count">{{ recentAssignmentJobs.length }}</span></h2></div>
            <button class="button dark" type="button" @click="goToAssignments()"><Plus :size="15" />执行作业</button>
          </div>

          <TaskTown :monitor="townMonitor" :tasks="recentAssignmentJobs" :accounts="accounts" :ready="taskSnapshotAt > 0" :online="Boolean(health?.ok)" @open-monitor="taskMonitor?.open()" @details="openOverviewTask" @delete="requestDeleteTask" @create="goToAssignments()" />
        </section>
        <aside class="overview-rail">
          <div class="section-title"><h3>平台账号</h3><span class="count">{{ accounts.length }}</span></div>
          <button v-for="account in accounts" :key="account.id" class="overview-account" type="button" @click="goToAccount(account.id)">
            <PlatformLogo :platform="account.platform" :size="36" /><span><strong>{{ account.name }}</strong><small><span class="account-dot" :class="account.status"></span>{{ accountStatusLabel(account.status) }}</small></span><ArrowUpRight :size="15" />
          </button>
          <button class="button ghost wide add-overview-account" type="button" @click="addPlatformAccount(selectedPlatformId)"><LogIn :size="15" />授权</button>
          <div class="overview-section">
            <div class="section-title"><h3>最近采集</h3></div>
            <div v-for="account in accounts.filter(item => item.last_collection_at)" :key="account.id" class="collection-summary"><div><span>{{ account.name }}</span><strong>{{ account.last_collection_count || 0 }} <small>待处理</small></strong></div><time>{{ formatLocalTime(account.last_collection_at) }}</time></div>
            <div v-if="!accounts.some(account => account.last_collection_at)" class="empty-inline">暂无采集记录</div>
          </div>
        </aside>
      </main>

      <PlatformWorkspace v-else-if="activePage === 'platforms'" key="platforms" v-model="selectedPlatformId" :platforms="platforms" :accounts="accounts" :selected-account-id="selectedAccountId" :menu-id="accountMenuId" :login-account-id="loginAccountId" :is-busy="accountOperationBusy" @add="addPlatformAccount" @authorize="authorizeAccount" @confirm-authorization="confirmPlatformAuthorization" @refresh-cookie="refreshAccountCookie" @delete="openDeleteAccount" @menu="toggleAccountMenu" @menu-keydown="navigateAccountMenu" @assignments="goToAssignments" @learning="goToLearning" />
      <AttendanceWorkspace v-else-if="activePage === 'attendance'" key="attendance" :accounts="accounts" @authorize="id => id ? goToAccount(id) : goToPage('platforms')"/>

      <main v-else-if="activePage === 'assignments'" key="assignments" class="platform-layout" :class="{ 'logs-open': logPanelOpen }" :style="{ '--assignment-width': `${listWidth}px` }">
        <aside class="account-rail assignment-account-rail">
          <div class="section-title account-title">
            <span>账号</span>
            <span class="count">{{ assignmentAccounts.length }}</span>
          </div>
          <button class="button light wide collect-all-accounts" type="button" :disabled="collectionBatchBusy || !assignmentAccounts.length" @click="collectAllAccounts"><RefreshCw :size="14" :class="{spinning: collectionBatchBusy}" />{{ collectionBatchBusy ? '采集中 ' + batchCollectionDone + '/' + collectionBatchRows.length : '一键采集' }}</button>
          <div
            v-for="account in assignmentAccounts"
            :key="account.id"
            class="account-item"
            :data-account-id="account.id"
            :class="{ active: account.id === selectedAccountId }"
          >
            <button
              class="account-select"
              type="button"
              @click="switchAccount(account.id)"
            >
              <AccountAvatar :account="account"><span class="account-dot" :class="account.status"></span></AccountAvatar>
              <span>
                <strong>{{ account.name }}</strong>
                <small>
                  {{ platformLabel(account.platform) }} ·
                  {{ account.learning_run ? '网课执行中' : accountCollections[account.id]?.pending || accountCollections[account.id]?.run?.status === 'running' || account.last_collection_status === 'running' ? '采集中' : accountStatusLabel(account.status) }}
                </small>
                <small v-if="batchCollectionById[account.id]" class="account-collection-state" :class="batchCollectionById[account.id].status" :title="batchCollectionById[account.id].message">{{ conciseMessage(batchCollectionById[account.id].message) }}</small>
              </span>
            </button>
          </div>
          <button class="button ghost wide account-manage-link" type="button" @click="goToAccount(selectedAccountId)"><Layers3 :size="14" />管理平台账号<ArrowUpRight :size="13" /></button>
          <p v-if="!assignmentAccounts.length" class="empty-inline">暂无支持作业的账号</p>
        </aside>

        <section class="assignment-board">
          <div class="board-heading">
            <div>
              <h2>作业 <span class="heading-count">{{ filteredAssignments.length }}</span></h2>
              <span v-if="selectedAccount" class="board-account-name"><PlatformLogo :platform="selectedAccount.platform" :size="15" />{{ selectedAccount.name }}</span>
            </div>
            <div class="filters">
              <label v-if="selectedAccount?.platform !== 'educoder'" class="semester-filter">
                <span>学期</span>
                <select v-model="selectedSemester" aria-label="学期">
                  <option value="">全部学期</option>
                  <option v-for="semester in semesters" :key="semester" :value="semester">
                    {{ semester }}
                  </option>
                </select>
              </label>
              <button class="icon-button" type="button" title="作业配置" aria-label="打开作业配置" @click="openSettings('assignments')"><Settings2 :size="16" /></button>
              <button
                class="button dark"
                type="button"
                :disabled="collecting || !selectedAccount || Boolean(selectedAccountActiveJob) || Boolean(selectedAccount?.learning_run)"
                @click="collectAssignments"
              >
                <RefreshCw :size="14" :class="{ spinning: collecting }" aria-hidden="true" />
                {{ selectedAccountActiveJob
                  ? "任务运行中"
                  : (collecting ? "采集中..." : "采集") }}
              </button>
            </div>
          </div>
          <EducoderCourses v-if="selectedAccount?.platform === 'educoder'" :key="selectedAccount.id" :account="selectedAccount" @changed="loadAssignments" />
          <div class="assignment-tools">
            <label class="search-field"><Search :size="14" /><input v-model="assignmentQuery" aria-label="搜索作业" placeholder="搜索作业、课程"><button v-if="assignmentQuery" type="button" class="icon-button" aria-label="清除作业搜索" @click="assignmentQuery = ''"><X :size="13" /></button></label>
            <div class="assignment-filter-row"><LeiTabs v-model="assignmentFilter" :items="assignmentFilterItems" label="作业筛选" /><label class="sort-control"><ArrowDownUp :size="13" /><select v-model="assignmentSort" aria-label="作业排序"><option value="course">按课程</option><option value="title">按名称</option></select></label></div>
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
                :style="{ transform: `scaleX(${(collectionRun.progress || 0) / 100})` }"
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
          <div v-if="filteredAssignments.length || selectedCount" class="selection-toolbar">
            <label>
              <input
                type="checkbox"
                :checked="allFilteredSelected"
                  :disabled="!selectableAssignments.length"
                @change="toggleSelectAllAssignments"
              >
              <span>全选</span>
            </label>
            <span>{{ selectedCount ? `已选 ${selectedCount}` : `${filteredAssignments.length} 个作业` }}</span>
            <button v-if="selectedCount" type="button" class="text-button" @click="selectedAssignmentIds = []">清除</button>
            <button
              class="button dark"
              type="button"
              :disabled="batchCreating || selectedAssignmentIds.length === 0"
              @click="createBatchJobs"
            >
              <Play :size="14" aria-hidden="true" />{{ batchCreating ? "批量创建中..." : "批量执行" }}
            </button>
          </div>
          <details v-if="batchFailures.length" class="batch-failures"><summary><CircleAlert :size="14" />{{ batchFailures.length }} 个任务创建失败</summary><p v-for="failure in batchFailures" :key="failure.id"><strong>{{ failure.title }}</strong>{{ failure.error }}</p></details>
          <div v-if="groupedAssignments.length === 0" class="empty-state">
            <ClipboardList :size="28" :stroke-width="1.4" aria-hidden="true" />
            <h3>{{ assignmentQuery || assignmentFilter !== 'all' ? '没有匹配的作业' : assignmentEmptyTitle() }}</h3>
            <button v-if="assignmentQuery || assignmentFilter !== 'all'" type="button" class="button light" @click="assignmentQuery = ''; assignmentFilter = 'all'">清除筛选</button>
            <p v-if="assignmentEmptyDescription()">{{ assignmentEmptyDescription() }}</p>
            <button
              v-if="selectedAccount && selectedAccount.status !== 'ready'"
              class="button light"
              type="button"
              @click="goToAccount(selectedAccount.id)"
            ><LogIn :size="14" aria-hidden="true" />前往授权</button>
            <button v-else-if="!selectedAccount" class="button light" type="button" @click="activePage = 'platforms'"><Plus :size="14" />添加平台账号</button>
            <button
              v-else-if="selectedAccount && !collecting"
              class="button light"
              type="button"
              :disabled="Boolean(selectedAccountActiveJob)"
              @click="collectAssignments"
            ><RefreshCw :size="14" aria-hidden="true" />采集作业</button>
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
                  :disabled="requiresMaterials(assignment)"
                  @change="toggleAssignmentSelection(assignment.id)"
                >
              </label>
              <div class="assignment-copy">
                <strong>{{ assignment.title }}</strong>
                <p v-if="assignment.deadline_text">
                  {{ assignment.deadline_text }}
                  <span
                    class="deadline-state"
                    :class="deadlineState(assignment).className"
                  >
                    {{ deadlineState(assignment).label }}
                  </span>
                </p>
                <span v-if="!latestJobForAssignment(assignment.id) || requiresMaterials(assignment)" class="assignment-readiness" :class="assignmentReadiness(assignment).className"><span>{{ assignmentReadiness(assignment).label }}</span><small v-if="assignmentReadiness(assignment).detail">{{ assignmentReadiness(assignment).detail }}</small></span>
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
                <button v-if="!assignment.assessment?.checked_at" class="icon-button" type="button" title="检查作答状态与材料要求" :aria-label="'检查要求：' + assignment.title" :disabled="inspectingAssignmentId === assignment.id" @click="inspectAssignment(assignment)"><RefreshCw :size="13" :class="{ spinning: inspectingAssignmentId === assignment.id }" /></button>
                <button
                  v-if="!requiresMaterials(assignment)"
                  class="assignment-view"
                  type="button"
                  :disabled="openingAssignmentId === assignment.id"
                  @click="openAssignment(assignment)"
                >
                  <ExternalLink :size="13" aria-hidden="true" />
                  {{ openingAssignmentId === assignment.id ? "打开中" : "查看" }}
                </button>
                <button
                  class="button light assignment-start"
                  type="button"
                  :disabled="busy"
                  @click="startAssignmentJob(assignment)"
                >
                  <component :is="requiresMaterials(assignment) ? FileText : assignmentJobLabel(assignment.id) === '重试' ? RotateCcw : assignmentJobLabel(assignment.id) === '查看任务' ? ClipboardList : Play" :size="13" aria-hidden="true" />
                  {{ requiresMaterials(assignment) ? '查看要求' : assignmentJobLabel(assignment.id) }}
                </button>
              </div>
            </article>
          </section>
        </section>
        <LeiResizeHandle v-model="listWidth" :min="300" :max="520" @commit="remember('list-width', listWidth)" />
        <aside
          class="profile-preview-panel platform-browser-panel"
          :class="{
            'live-task-panel': browserShowsCapture,
            'active-browser-panel': Boolean(selectedAccountActiveJob),
            expanded: browserExpanded
          }"
        >
          <div class="profile-preview-heading">
            <div>
              <span class="panel-label"><PlatformLogo :platform="selectedAccount?.platform || 'chaoxing'" :size="20" />{{ selectedAccount?.name || '浏览器' }}<span class="browser-mode-label">{{ selectedAccountActiveJob ? '实时会话' : browserShowsCapture ? '任务画面' : '当前会话' }}</span></span>
              <h2>{{ browserShowsCapture ? platformLiveJob?.assignment_title : (profilePreviewTitle || '浏览器') }}</h2>
            </div>
            <div class="preview-actions">
              <button class="button dark browser-entry-button" type="button" :disabled="!selectedAccount || selectedAccount.status !== 'ready' || Boolean(selectedAccountActiveJob && !canJobAction(selectedAccountActiveJob, 'open_browser') && !canJobAction(selectedAccountActiveJob, 'view_browser'))" @click="openWorkspaceBrowser"><ExternalLink :size="14" />进入浏览器</button>
              <button v-if="canJobAction(selectedAccountActiveJob, 'pause')" :disabled="jobPending(selectedAccountActiveJob)" class="icon-button" type="button" title="暂停任务" aria-label="暂停任务" @click="controlSelectedJob('pause', { jobId: selectedAccountActiveJob.id })"><Pause :size="15" /></button>
              <button v-if="canJobAction(selectedAccountActiveJob, 'resume')" :disabled="jobPending(selectedAccountActiveJob)" class="icon-button" type="button" title="继续任务" aria-label="继续任务" @click="controlSelectedJob('resume', { jobId: selectedAccountActiveJob.id })"><Play :size="15" /></button>
              <button v-if="canJobAction(selectedAccountActiveJob, 'stop')" :disabled="jobPending(selectedAccountActiveJob)" class="icon-button danger" type="button" title="停止任务" aria-label="停止任务" @click="controlSelectedJob('stop', { jobId: selectedAccountActiveJob.id })"><Square :size="14" /></button>
              <button v-if="canJobAction(selectedAccountActiveJob, 'continue')" class="button dark" :disabled="jobPending(selectedAccountActiveJob)" type="button" @click="controlSelectedJob('continue', { jobId: selectedAccountActiveJob.id })"><Play :size="14" />人工处理后继续</button>
              <button
                v-if="canJobAction(selectedAccountActiveJob, 'close_browser')"
                class="button dark"
                type="button"
                @click="finishSelectedIntervention(selectedAccountActiveJob.id)"
                :disabled="jobPending(selectedAccountActiveJob)"
              >
                <CheckCheck :size="14" aria-hidden="true" />关闭浏览器
              </button>
              <button
                v-if="platformLiveJob"
                class="icon-button"
                type="button"
                title="任务详情"
                aria-label="任务详情"
                @click="openTaskDetails(platformLiveJob.id)"
              >
                <ClipboardList :size="15" aria-hidden="true" />
              </button>
              <button class="icon-button" type="button" :aria-pressed="logPanelOpen" title="切换执行日志" aria-label="切换执行日志" @click="logPanelOpen = !logPanelOpen"><PanelRightClose v-if="logPanelOpen" :size="16" /><PanelRightOpen v-else :size="16" /></button>
              <button class="icon-button" type="button" :aria-pressed="browserExpanded" :title="browserExpanded ? '收起浏览器画面' : '展开浏览器画面'" :aria-label="browserExpanded ? '收起浏览器画面' : '展开浏览器画面'" @click="toggleBrowserExpansion"><Minimize2 v-if="browserExpanded" :size="16" /><Maximize2 v-else :size="16" /></button>
            </div>
          </div>

          <div v-if="platformLiveJob && !platformLiveJob.browser_available && !selectedAccountActiveJob" class="browser-view-switch"><LeiTabs v-model="captureMode" :items="[{value: 'task', label: '最终结果'}, {value: 'profile', label: '当前会话'}]" label="浏览器画面来源" /></div>
          <label v-if="accountTaskChoices.length > 1" class="task-browser-selector"><span>作业窗口</span><select :value="platformLiveJob?.id || ''" aria-label="选择作业任务窗口" @change="selectJob($event.target.value); captureMode = 'task'"><option v-for="task in accountTaskChoices" :key="task.id" :value="task.id">{{ task.assignment_title }} · {{ jobStatusLabel(task.status) }}</option></select></label>
          <LiveBrowser v-if="platformLiveJob?.browser_available" ref="assignmentViewer" :native-browser="platformLiveJob.platform !== 'educoder'" :run-id="platformLiveJob.id" kind="assignment" @open-browser="focusAgentBrowser(platformLiveJob.id)" />
          <template v-else-if="browserShowsCapture">
            <div class="platform-job-summary">
              <span class="job-badge" :class="platformLiveJob.status">
                {{ jobStatusLabel(platformLiveJob.status) }}
              </span>
              <span>{{ platformLiveJob.progress || 0 }}%</span>
              <span>{{ submissionStatusLabel(platformLiveJob.submission_status) }}</span>
            </div>
            <div v-if="selectedAccountActiveJob" class="platform-agent-state">
              <strong>{{ currentAgentState?.message || "等待 Agent 日志" }}</strong>
              <p v-if="actionSummary(currentAgentState?.data?.actions, currentAgentState?.data?.action_details)">
                {{ actionSummary(currentAgentState.data.actions, currentAgentState.data.action_details) }}
              </p>

            </div>
            <div
              class="platform-live-image"
              tabindex="0"
              role="region"
              :aria-label="selectedAccountActiveJob ? '实时任务画面' : '已保存的任务画面'"
              :title="selectedAccountActiveJob?.browser_available ? '双击进入实时浏览器' : '已保存的任务画面，可滚动查看'"
              @dblclick="selectedAccountActiveJob?.browser_available && openWorkspaceBrowser()"
            >
              <ScreenshotViewer v-if="screenshot" :image="screenshot" :segments="screenshotSections" />
              <div v-else class="profile-preview-empty">
                {{ taskDetailsLoading ? "正在加载任务画面..." : selectedAccountActiveJob ? "等待 Agent 首帧画面..." : "没有可显示的页面截图" }}
              </div>
            </div>
          </template>

          <template v-else>
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
          </template>
        </aside>
        <aside v-if="logPanelOpen" class="platform-log-panel">
          <div class="platform-log-heading">
            <div>
              <h2><FileText :size="16" aria-hidden="true" />执行日志</h2>
            </div>
            <span class="count" :title="`已完成 ${completedStepCount} 个动作步骤`">
              {{ completedStepCount }}
            </span>
            <button class="icon-button" type="button" title="收起执行日志" aria-label="收起执行日志" @click="logPanelOpen = false"><X :size="15" /></button>
          </div>
          <template v-if="platformLiveJob">
            <div v-if="taskDetailsLoading" class="empty-inline" role="status">正在读取任务状态...</div>
            <div v-else class="workflow-stages">
              <div
                v-for="stage in workflowStages"
                :key="stage.id"
                class="workflow-stage"
                :class="stage.state"
              >
                <span class="workflow-stage-dot">
                  <Check v-if="stage.state === 'done'" :size="12" aria-hidden="true" />
                  <LoaderCircle v-else-if="stage.state === 'active'" :size="13" class="spinning" aria-hidden="true" />
                  <CircleAlert v-else-if="['error', 'attention'].includes(stage.state)" :size="13" aria-hidden="true" />
                  <Circle v-else :size="10" aria-hidden="true" />
                </span>
                <div>
                  <strong>{{ stage.label }}</strong>
                  <small>{{ stage.detail }}</small>
                </div>
              </div>
            </div>
            <div class="platform-log-stream">
              <TransitionGroup name="log-pop" tag="div" appear>
                <article
                  v-for="event in visibleEvents.slice(0, 20)"
                  :key="`platform-${event.id}`"
                >
                  <span class="event-dot" :class="event.kind"></span>
                  <div>
                    <div class="platform-log-event-meta">
                      <span v-if="event.step_number" class="platform-log-step">
                        #{{ event.step_number }}
                      </span>
                      <span class="platform-log-state" :class="event.kind">
                        {{ event.state_label }}
                      </span>
                    </div>
                    <strong>{{ event.message || event.kind }}</strong>
                    <p v-if="actionSummary(event.data?.actions, event.data?.action_details)">
                      {{ actionSummary(event.data.actions, event.data.action_details) }}
                    </p>
                    <small>{{ new Date(event.created_at).toLocaleTimeString("zh-CN") }}</small>
                  </div>
                </article>
              </TransitionGroup>
              <div v-if="visibleEvents.length === 0" class="empty-inline">
                {{ taskDetailsLoading ? '正在读取执行日志...' : '暂无执行日志' }}
              </div>
            </div>
            <div v-if="artifacts.length" class="platform-log-artifacts">
              <button v-if="screenshot" class="text-button" type="button" @click="openTaskDetails(selectedJobId)">查看截图<template v-if="screenshotSections.length"> · {{ screenshotSections.length }} 段</template></button>
              <a
                v-for="artifact in artifacts.filter(item => !['screenshot', 'final_screenshot', 'live_screenshot'].includes(item.kind)).slice(-3)"
                :key="artifact.id"
                :href="api.artifactUrl(artifact.id)"
                target="_blank"
                rel="noreferrer"
              >
                {{ artifactTitle(artifact) }}
              </a>
            </div>
          </template>
          <div v-else class="platform-log-empty">
            暂无执行记录
          </div>
        </aside>
      </main>

      <LearningWorkspace v-else-if="activePage === 'learning'" key="learning" :accounts="learningAccounts" :initial-course-id="learningCourseId" v-model:account-id="selectedAccountId" @refresh="refreshTaskOverview" @result="openLearningResult" @platforms="activePage = 'platforms'" />

      <main v-else-if="activePage === 'connectors'" key="connectors" class="settings-page">
        <div class="page-heading">
          <h2>连接器</h2>
        </div>
        <div class="connector-grid">
          <button
            v-for="connector in connectors.filter(item => item.available)"
            :key="connector.id"
            type="button"
            class="connector-card"
            :class="{ available: connector.available }"
            :disabled="!connector.available"
            @click="openConnector(connector)"
          >
            <PlatformLogo :platform="connector.id" :size="44" />
            <span>
              <strong>{{ connector.name }}</strong>
            </span>
            <span
              v-if="connector.id === 'weixin'"
              class="status-tag"
              :class="{ enabled: settingsInfo?.weixin_configured }"
            >
              {{ settingsInfo?.weixin_ready ? "会话就绪" : settingsInfo?.weixin_configured ? "已绑定" : "配置" }}
            </span>
            <span v-else class="status-tag">未接入</span>
          </button>
        </div>
      </main>

      </Transition>
    </div>

    <SettingsDialog :open="settingsOpen" v-model:section="settingsSection" :accounts="accounts" :info="settingsInfo" :profile="localProfile" :save-profile="updateLocalProfile" :update="updateState" :auto-check="autoCheckUpdates" :check-updates="checkForUpdates" :set-auto-check="updateAutoCheck" :open-external="openProjectLink" :open-data="openDataDirectory" :open-logs="openLogDirectory" @close="settingsOpen=false" @saved="preferencesSaved" @model-saved="modelSettingsSaved" @setup="reopenSetup" @platforms="settingsOpen=false;activePage='platforms'" />
    <LearningTaskDetails :open="learningResultOpen" :run-id="learningResultId" @close="learningResultOpen = false" @workspace="openLearningWorkspace" />

    <Transition name="dialog">
      <div
        v-if="taskDetailsOpen && selectedJob"
        class="modal-backdrop"
        @click.self="taskDetailsOpen = false"
      >
        <section
          class="modal-card task-details-modal"
          role="dialog"
          aria-modal="true"
          aria-labelledby="task-details-title"
          :aria-busy="taskDetailsLoading"
          tabindex="-1"
        >
          <div class="agent-heading task-details-heading">
            <div>
              <h2 id="task-details-title">{{ selectedJob.assignment_title }}</h2>
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
              <button
                id="task-details-close"
                class="modal-close"
                type="button"
                aria-label="关闭"
                title="关闭"
                @click="taskDetailsOpen = false"
              >
                <X :size="17" aria-hidden="true" />
              </button>
            </div>
          </div>
          <div class="task-details-toolbar">
            <span>{{ selectedJob.account_name }} · {{ selectedJob.course_name }}</span>
            <label class="task-attempt-picker">
              执行记录
              <select
                aria-label="执行记录"
                :value="selectedJobId"
                @change="selectJob($event.target.value)"
              >
                <option v-for="job in selectedJobAttempts" :key="job.id" :value="job.id">
                  第 {{ job.attempt || 1 }} 次 · {{ jobStatusLabel(job.status) }} ·
                  {{ formatLocalTime(job.created_at) }}{{ job.id === selectedJobAttempts[0]?.id ? '（最新）' : '' }}
                </option>
              </select>
            </label>
          </div>
          <div v-if="selectedJob" class="job-controls">
            <button
              v-if="canJobAction(selectedJob, 'open_browser') || canJobAction(selectedJob, 'view_browser')"
              class="button dark browser-entry-button"
              type="button"
              @click="focusAgentBrowser()"
            >
              <Monitor :size="14" aria-hidden="true" />进入实时浏览器
            </button>
            <button
              v-if="canJobAction(selectedJob, 'close_browser')"
              class="button dark"
              type="button"
              @click="finishSelectedIntervention()"
              :disabled="jobPending()"
            >
              <CheckCheck :size="14" aria-hidden="true" />关闭浏览器
            </button>
            <button
              v-if="canJobAction(selectedJob, 'pause')"
              class="button light"
              type="button"
              @click="controlSelectedJob('pause')"
              :disabled="jobPending()"
            >
              <Pause :size="14" aria-hidden="true" />暂停
            </button>
            <button
              v-if="canJobAction(selectedJob, 'resume')"
              class="button dark"
              type="button"
              @click="controlSelectedJob('resume')"
              :disabled="jobPending()"
            >
              <Play :size="14" aria-hidden="true" />继续
            </button>
            <button
              v-if="canJobAction(selectedJob, 'stop')"
              class="button light"
              type="button"
              @click="controlSelectedJob('stop')"
              :disabled="jobPending()"
            >
              <Square :size="14" aria-hidden="true" />停止
            </button>
            <button
              v-if="canJobAction(selectedJob, 'retry')"
              class="button light"
              type="button"
              @click="requestRetry(selectedJob)"
              :disabled="jobPending()"
            >
              <RotateCcw :size="14" aria-hidden="true" />重试
            </button>
              <button v-if="canJobAction(selectedJob, 'continue')" class="button dark" type="button" :disabled="jobPending()" @click="controlSelectedJob('continue')"><Play :size="14" />人工处理后继续</button>
          </div>
          <div v-if="jobPending()" class="job-action-feedback" role="status"><LoaderCircle :size="14" class="spinning" />正在处理当前操作，请等待状态返回...</div>
          <div v-if="selectedJob.waiting_reason" class="job-queue-blocker"><CircleAlert :size="16" /><span>{{ selectedJob.waiting_reason }}</span><button v-if="selectedJob.blocked_by_job_id" type="button" class="text-button" @click="openTaskDetails(selectedJob.blocked_by_job_id)">查看占用任务</button></div>
          <div v-if="taskDetailsLoading" class="empty-inline" role="status">正在加载任务状态…</div>
          <div v-else class="agent-content-grid">
            <div class="agent-visual-column">
              <div
                v-if="selectedJob && !isActiveJob(selectedJob)"
                class="job-result-card"
                :class="selectedJob.status"
              >
                <div>
                  <span>任务结果</span>
                  <strong>{{ submissionStatusLabel(selectedJob.submission_status) }}</strong>


                  <small v-if="selectedJob.verified_at">
                    人工核对：{{ formatLocalTime(selectedJob.verified_at) }}
                    <template v-if="selectedJob.verification_note">
                      · {{ selectedJob.verification_note }}
                    </template>
                  </small>
            </div>
            <p v-if="isNeedsIntervention(selectedJob)" class="result-intervention">
              {{ selectedJobIssue }}
            </p>
            <p v-else-if="selectedJobIssue" :class="{ 'result-error': selectedJob.error }">{{ selectedJobIssue }}</p>
            <p v-else>暂无执行摘要</p>
            <div class="result-actions">
              <button
                v-if="canJobAction(selectedJob, 'open_assignment')"
                class="button light"
                type="button"
                @click="openSelectedJobAssignment"
              >
                <ExternalLink :size="14" aria-hidden="true" />打开作业核对
              </button>
              <button
                v-if="canJobAction(selectedJob, 'confirm_filled')"
                class="button dark"
                type="button"
                :disabled="jobPending()"
                @click="verifySelectedSubmission('filled_verified')"
              >
                <CheckCheck :size="14" aria-hidden="true" />确认已核对
              </button>
              <button
                v-if="canJobAction(selectedJob, 'confirm_submitted')"
                class="button dark"
                type="button"
                :disabled="jobPending()"
                @click="verifySelectedSubmission('submitted_verified')"
              >
                我已提交
              </button>
              <button
                v-if="canJobAction(selectedJob, 'confirm_not_submitted')"
                class="button light"
                type="button"
                :disabled="jobPending()"
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
              :style="{ transform: `scaleX(${(selectedJob?.progress || 0) / 100})` }"
            ></span>
          </div>
          <div
            v-if="selectedJob && isActiveJob(selectedJob)"
            class="agent-current-state"
            aria-live="polite"
          >
            <strong>{{ currentAgentState?.message || "等待 Agent 日志" }}</strong>
            <p v-if="actionSummary(currentAgentState?.data?.actions, currentAgentState?.data?.action_details)">
              {{ actionSummary(currentAgentState.data.actions, currentAgentState.data.action_details) }}
            </p>

            <small v-if="currentAgentState">
              {{ new Date(currentAgentState.created_at).toLocaleTimeString("zh-CN") }}
            </small>
          </div>
          <section v-if="selectedJob" class="agent-live-preview">
            <div class="agent-live-preview-heading">
              <LeiTabs v-if="selectedJob.browser_available && screenshot" v-model="detailBrowserTab" :items="[{value: 'live', label: '实时浏览器'}, {value: 'result', label: '最终结果'}]" label="任务浏览器视图" />
              <span v-else>{{ selectedJob.browser_available ? '实时浏览器' : '最终结果' }}</span>
            </div>
            <div class="agent-browser-surface">
              <LiveBrowser v-if="selectedJob.browser_available && detailBrowserTab === 'live'" ref="detailViewer" :native-browser="selectedJob.platform !== 'educoder'" :run-id="selectedJob.id" kind="assignment" @open-browser="focusAgentBrowser()" />
              <ScreenshotViewer v-else-if="screenshot" :image="screenshot" :segments="screenshotSections" />
              <div v-else class="profile-preview-empty">{{ isActiveJob(selectedJob) ? '正在连接任务浏览器…' : '没有保存的执行截图' }}</div>
            </div>
          </section>
            </div>
            <div class="agent-activity-column">
              <div class="timeline" role="log" aria-live="polite" aria-relevant="additions text">
                <div v-for="event in visibleEvents" :key="event.id" class="timeline-item">
                  <span class="event-dot" :class="event.kind"></span>
                  <div>
                    <strong>{{ event.message || event.kind }}</strong>
                    <p v-if="actionSummary(event.data?.actions, event.data?.action_details)">
                      {{ actionSummary(event.data.actions, event.data.action_details) }}
                    </p>

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
              :href="api.artifactUrl(artifact.id)"
              target="_blank"
              rel="noreferrer"
            >
              {{ artifactTitle(artifact) }}
            </a>
          </div>
            </div>
          </div>
        </section>
      </div>
    </Transition>

    <Transition name="dialog">
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
              <h2 id="launch-confirm-title">确认允许最终提交</h2>
            </div>
            <button class="modal-close" type="button" aria-label="关闭" title="关闭" @click="launchConfirmOpen = false">
              <X :size="17" aria-hidden="true" />
            </button>
          </div>
          <p class="modal-message">
            将执行 {{ pendingLaunch.assignments.length }} 个作业并允许最终提交。
            仅在页面核验到提交成功后标记“已完成”；无法确认时保留待核对结果。
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
              确认执行并允许提交
            </button>
          </div>
        </div>
      </div>
    </Transition>



    <Transition name="dialog">
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
              <h2 id="weixin-modal-title"><PlatformLogo platform="weixin" :size="28" />微信机器人</h2>
            </div>
            <button class="modal-close" type="button" aria-label="关闭" title="关闭" @click="weixinModalOpen = false"><X :size="17" aria-hidden="true" /></button>
          </div>
          <div v-if="weixinMode === 'loading'" class="weixin-loading" role="status"><LoaderCircle class="spinning" :size="20" />正在读取连接器</div>
          <template v-else-if="weixinMode === 'settings' && weixinConfig">
            <div class="weixin-binding-status"><span class="connection-dot" :class="{ online: weixinConfig.ready }"></span>{{ weixinConfig.ready ? '会话就绪' : '已绑定，等待刷新会话' }}<button class="text-button" type="button" :disabled="weixinSessionBusy || weixinPreferencesBusy" @click="refreshWeixinSession"><RefreshCw :size="13" :class="{ spinning: weixinSessionBusy }" />刷新会话</button></div>
            <div class="weixin-preference-row">
              <label><span>新作业提醒</span><input class="switch-input" type="checkbox" role="switch" aria-label="新作业提醒" :checked="weixinConfig.preferences.new_assignments_enabled" :disabled="weixinPreferencesBusy || weixinSessionBusy" @change="updateWeixinPreference('new_assignments_enabled', $event.target.checked)"></label>
              <button class="text-button" type="button" @click="openCollectionSettings">检测设置<ArrowUpRight :size="13" /></button>
            </div>
            <div class="weixin-preference-row"><label><span>任务结果</span><input class="switch-input" type="checkbox" role="switch" aria-label="任务结果" :checked="weixinConfig.preferences.completion_enabled" :disabled="weixinPreferencesBusy || weixinSessionBusy" @change="updateWeixinPreference('completion_enabled', $event.target.checked)"></label></div>
            <details v-if="weixinConfig.examples" class="weixin-message-preview"><summary>消息预览</summary><pre>{{ weixinConfig.examples.new_assignment }}</pre><pre>{{ weixinConfig.examples.completion }}</pre></details>
            <p v-if="weixinConfig.preferences.new_assignments_enabled && weixinConfig.pending_new_assignments" class="modal-message" role="status">{{ weixinConfig.pending_new_assignments }} 个新增作业待推送。{{ weixinConfig.last_delivery_error || '下次定时采集时重试。' }}</p>
            <p v-if="!weixinConfig.ready" class="modal-message">在微信中向机器人发送一条消息，再刷新会话。</p>
            <div class="modal-actions split"><button class="text-button" type="button" :disabled="weixinSessionBusy || weixinPreferencesBusy" @click="openWeixinBinding">重新绑定</button><button class="button dark" type="button" @click="weixinModalOpen = false">完成</button></div>
          </template>
          <div v-if="weixinMode === 'binding'" class="qr-box">
            <img v-if="weixinQr" :src="weixinQr" alt="微信机器人绑定二维码">
            <span v-else>正在获取二维码...</span>
          </div>
          <p class="modal-message">{{ weixinMessage }}</p>
          <div v-if="weixinMode === 'binding' && weixinStatus === 'need_verifycode'" class="inline-form">
            <input v-model="weixinVerifyCode" inputmode="numeric" placeholder="手机验证码">
            <button class="button dark" type="button" @click="pollWeixinStatus">提交</button>
          </div>
          <div v-if="weixinMode === 'error' || (weixinMode === 'binding' && weixinStatus === 'error')" class="modal-actions"><button class="button light" type="button" @click="openWeixinConnector"><RefreshCw :size="14" />重试</button></div>
        </div>
      </div>
    </Transition>





    <AccountAuthorization :open="authorizationOpen" :state="authorizationState" :platform-label="platformLabel(authorizationState.platform)" @close="authorizationOpen = false" @retry="authorizationFlow.retry()" />
    <StartupExperience v-if="setupOpen" :can-close="setupDismissible" @close="setupOpen=false" :scope="setupScope" :accounts="accounts" :model-configured="Boolean(health?.ai_configured)" @ready="setupRuntimeReady" @complete="finishSetup" @model-saved="modelSettingsSaved" @authorized="accounts = $event" />

    <Transition name="dialog">
      <div v-if="deleteTaskTarget" class="modal-backdrop" @mousedown.self="!deleteTaskBusy && (deleteTaskTarget = null)">
        <section class="modal-card modal-narrow" role="dialog" aria-modal="true" aria-labelledby="delete-task-title" :aria-busy="deleteTaskBusy" tabindex="-1">
          <div class="modal-heading"><h2 id="delete-task-title">删除任务</h2><button class="modal-close" type="button" aria-label="关闭" title="关闭" :disabled="deleteTaskBusy" @click="deleteTaskTarget = null"><X :size="17" /></button></div>
          <strong class="delete-task-name">{{ deleteTaskTarget.assignment_title }}</strong>
          <p class="modal-message">将移除 {{ deleteTaskTarget.account_name }} 的这项任务及全部执行记录，保留维护日志。平台上的作业、课程和已保存进度不受影响。</p>
          <p v-if="deleteTaskError" class="validation-error" role="alert">{{ deleteTaskError }}</p>
          <div class="modal-actions"><button id="cancel-delete-task" class="button light" type="button" :disabled="deleteTaskBusy" @click="deleteTaskTarget = null">取消</button><button class="button danger-button" type="button" :disabled="deleteTaskBusy" @click="confirmDeleteTask"><LoaderCircle v-if="deleteTaskBusy" :size="14" class="spin" />{{ deleteTaskBusy ? '删除中…' : '删除任务' }}</button></div>
        </section>
      </div>
    </Transition>

    <Transition name="dialog">
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
              <h2 id="delete-modal-title">确认删除 {{ deleteAccountName }}</h2>
            </div>
            <button class="modal-close" type="button" aria-label="关闭" title="关闭" @click="deleteModalOpen = false"><X :size="17" aria-hidden="true" /></button>
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
    </Transition>
  </div>
</template>
