<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import QRCode from "qrcode";
import { api } from "./api";

const navItems = [
  { id: "dashboard", label: "面板", marker: "01" },
  { id: "platforms", label: "平台", marker: "02" },
  { id: "connectors", label: "连接器", marker: "03" },
  { id: "settings", label: "设置", marker: "04" }
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
const health = ref(null);
const settingsInfo = ref(null);
const platforms = ref([]);
const selectedAccountId = ref("");
const selectedSemester = ref("");
const selectedJobId = ref("");
const accountName = ref("");
const notice = ref("");
const busy = ref(false);
const error = ref("");
const screenshot = ref("");
const profilePreview = ref("");
const profilePreviewUrl = ref("");
const profilePreviewTitle = ref("");
const autoSubmit = ref(false);
const interactiveBrowser = ref(true);
const selectedAssignmentIds = ref([]);
const batchCreating = ref(false);
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
  models: []
});
const modelCatalog = ref([]);
const newModelName = ref("");
const modelMessage = ref("");
let socket = null;
let loginTimer = null;
let weixinTimer = null;
let profilePreviewTimer = null;
let connectionTimer = null;
let connecting = false;

const selectedAccount = computed(() => (
  accounts.value.find((account) => account.id === selectedAccountId.value) || null
));

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
  .filter((event) => event.kind !== "screenshot" || event.message)
  .slice()
  .reverse());

const currentAgentState = computed(() => visibleEvents.value[0] || null);

const jobStatusLabels = {
  queued: "排队中",
  running: "运行中",
  paused: "已暂停",
  stopping: "停止中",
  stopped: "已停止",
  completed: "已完成",
  failed: "失败",
  interrupted: "已中断"
};

function jobStatusLabel(status) {
  return jobStatusLabels[status] || status || "未知";
}

const connectionLabel = computed(() => {
  if (!health.value) return "后端离线";
  return health.value.ai_configured ? `${health.value.model} 已连接` : "AI 未配置";
});

function setError(value) {
  error.value = value instanceof Error ? value.message : String(value || "");
}

async function loadHealth() {
  try {
    health.value = await api.health();
  } catch (loadError) {
    health.value = null;
    setError(loadError);
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
    models: [...(config.models || [])]
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
    return;
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
    : 2026;
  assignments.value = await api.assignments({
    accountId: selectedAccountId.value || undefined,
    academicYear,
    submittable: true
  });
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
    await Promise.all([loadSemesters(), loadAssignments()]);
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
    notice.value = "账号已添加，请点击账号右侧“授权”完成登录。";
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
    accountMenuId.value = "";
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
  busy.value = true;
  error.value = "";
  try {
    const academicYear = selectedSemester.value
      ? Number(selectedSemester.value.slice(0, 4))
      : 2026;
    const result = await api.collect(selectedAccountId.value, academicYear);
    await Promise.all([loadSemesters(), loadAssignments()]);
    notice.value = `无头采集完成：${result.submittable} 个当前可提交作业`;
    activePage.value = "platforms";
  } catch (collectError) {
    setError(collectError);
  } finally {
    busy.value = false;
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
  const selected = filteredAssignments.value.filter((assignment) => (
    selectedAssignmentIds.value.includes(assignment.id)
  ));
  if (selected.length === 0) {
    notice.value = "请先选择至少一个作业。"
    return
  }
  batchCreating.value = true
  busy.value = true
  error.value = ""
  const createdJobs = []
  const failures = []
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
  notice.value = failures.length
    ? `已创建 ${createdJobs.length} 个任务，${failures.length} 个失败`
    : `已创建 ${createdJobs.length} 个 Agent 任务`
  batchCreating.value = false
  busy.value = false
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

async function selectJob(jobId) {
  selectedJobId.value = jobId;
  events.value = [];
  artifacts.value = [];
  screenshot.value = "";
  if (socket) {
    socket.close();
    socket = null;
  }
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

  socket = new WebSocket(api.wsUrl(jobId));
  socket.onmessage = async (message) => {
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
      "error"
    ].includes(event.kind)) {
      const updatedJob = await api.job(jobId);
      jobs.value = [
        updatedJob,
        ...jobs.value.filter((job) => job.id !== updatedJob.id)
      ];
    }
    if (["stopped", "completed", "error"].includes(event.kind)) {
      artifacts.value = await api.artifacts(jobId);
    }
  };
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

function goToPage(pageId) {
  activePage.value = pageId;
}

watch(selectedAccountId, async () => {
  if (!selectedAccountId.value) return;
  selectedAssignmentIds.value = [];
  await Promise.all([loadSemesters(), loadAssignments()]);
  startProfilePreview();
});

watch(selectedSemester, () => {
  selectedAssignmentIds.value = [];
  loadAssignments();
});

watch(activePage, () => {
  startProfilePreview();
});

onMounted(async () => {
  document.addEventListener("click", closeAccountMenuOnOutsideClick);
  document.addEventListener("keydown", closeAccountMenuOnEscape);
  await connectBackend();
  connectionTimer = setInterval(async () => {
    if (!health.value) {
      await connectBackend();
    }
  }, 5000);
});

onUnmounted(() => {
  document.removeEventListener("click", closeAccountMenuOnOutsideClick);
  document.removeEventListener("keydown", closeAccountMenuOnEscape);
  if (socket) socket.close();
  if (loginTimer) clearInterval(loginTimer);
  if (weixinTimer) clearInterval(weixinTimer);
  if (profilePreviewTimer) clearInterval(profilePreviewTimer);
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

    <div v-if="error" class="error-strip">
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
          <span class="nav-marker">{{ item.marker }}</span>
          <span>{{ item.label }}</span>
        </button>
        <div class="side-nav-footer">
          <span>LOCAL</span>
          <small>v0.1.0</small>
        </div>
      </nav>

      <div v-if="notice" class="notice-line">{{ notice }}</div>

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
              <span>可提交作业</span>
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
              <small>{{ job.course_name }} · {{ job.progress }}%</small>
            </span>
            <span class="job-card-status">{{ jobStatusLabel(job.status) }}</span>
          </button>
        </section>

        <aside class="agent-panel dashboard-agent-panel">
          <div class="agent-heading">
            <div>
              <span class="eyebrow">实时执行</span>
              <h2>{{ selectedJob?.assignment_title || "Agent 监控" }}</h2>
            </div>
            <span v-if="selectedJob" class="job-badge" :class="selectedJob.status">
              {{ jobStatusLabel(selectedJob.status) }}
            </span>
          </div>
          <div v-if="selectedJob" class="job-controls">
            <button
              v-if="selectedJob.status === 'running'"
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
              v-if="['failed','stopped','completed','interrupted'].includes(selectedJob.status)"
              class="button light"
              type="button"
              @click="controlSelectedJob('retry')"
            >
              重试
            </button>
          </div>
          <div class="progress-track">
            <span :style="{ width: `${selectedJob?.progress || 0}%` }"></span>
          </div>
          <div class="agent-current-state">
            <strong>{{ currentAgentState?.message || "等待 Agent 日志" }}</strong>
            <p v-if="currentAgentState?.data?.url">{{ currentAgentState.data.url }}</p>
            <small v-if="currentAgentState">
              {{ new Date(currentAgentState.created_at).toLocaleTimeString("zh-CN") }}
            </small>
          </div>
          <div class="timeline">
            <div v-for="event in visibleEvents" :key="event.id" class="timeline-item">
              <span class="event-dot" :class="event.kind"></span>
              <div>
                <strong>{{ event.message || event.kind }}</strong>
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
              {{ artifact.kind }}
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
          <button class="button dark wide" type="button" @click="accountModalOpen = true">
            添加
          </button>
          <div class="section-title account-title">
            <span>账号</span>
            <span class="count">{{ accounts.length }}</span>
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
                <small>{{ account.platform }} · {{ account.status }}</small>
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
                <button type="button" @click.stop="refreshAccountCookie(account)">
                  刷新 Cookie
                </button>
                <button type="button" @click.stop="openRenameAccount(account)">
                  重命名
                </button>
                <button
                  class="danger"
                  type="button"
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
                <span>允许 Agent 最终提交</span>
              </label>
              <label class="submit-toggle">
                <input v-model="interactiveBrowser" type="checkbox">
                <span>可操控浏览器</span>
              </label>
              <button
                class="button dark"
                type="button"
                :disabled="busy || !selectedAccount"
                @click="collectAssignments"
              >
                采集
              </button>
            </div>
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
            <h3>{{ selectedAccount?.status === "ready" ? "暂无可提交作业" : "账号尚未授权" }}</h3>
            <p>
              {{ selectedAccount?.status === "ready"
                ? "执行采集后显示当前可提交作业。"
                : "通过账号右侧“...”菜单刷新 Cookie 完成授权。" }}
            </p>
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
                <p>{{ assignment.deadline_text || "未显示截止时间" }}</p>
              </div>
              <div class="assignment-actions">
                <a :href="assignment.task_url" target="_blank" rel="noreferrer">查看</a>
                <button
                  class="button dark"
                  type="button"
                  :disabled="busy"
                  @click="createJob(assignment)"
                >
                  启动 Agent
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
              <button class="button dark" type="button" @click="openInteractiveBrowser">
                打开可操控浏览器
              </button>
              <button class="button light" type="button" @click="refreshProfilePreview(true)">
                刷新
              </button>
            </div>
          </div>
          <div class="profile-preview-url">{{ profilePreviewUrl || "等待浏览器画面" }}</div>
          <div class="profile-preview-image">
            <img v-if="profilePreview" :src="profilePreview" alt="当前账号浏览器页面">
            <div v-else class="profile-preview-empty">
              {{ profilePreviewTitle || "选择账号后加载浏览器页面" }}
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
          <span class="eyebrow">本地运行</span>
          <h2>设置</h2>
          <p>所有数据、日志和执行凭证均保存在本机。</p>
        </div>
        <section class="settings-row">
          <div>
            <strong>模型</strong>
            <p>{{ settingsInfo?.provider_name || "custom" }} · {{ settingsInfo?.openai_model || "未读取" }}</p>
          </div>
          <div class="settings-actions">
            <span class="status-tag" :class="{ enabled: settingsInfo?.ai_configured }">
              {{ settingsInfo?.ai_configured ? "已连接" : "未配置" }}
            </span>
            <button class="button light" type="button" @click="openModelModal">配置模型</button>
          </div>
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
        </section>
      </main>
    </div>

    <div
      v-if="accountModalOpen"
      class="modal-backdrop"
      @mousedown.self="accountModalOpen = false"
    >
      <div class="modal-card">
        <div class="modal-heading">
          <div>
            <span class="eyebrow">账号授权</span>
            <h2>添加平台账号</h2>
          </div>
          <button class="modal-close" type="button" @click="accountModalOpen = false">关闭</button>
        </div>
        <label class="field-label">
          <span>名称</span>
          <input v-model="accountForm.name" placeholder="例如：我的学习通">
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
      <div class="modal-card modal-narrow">
        <div class="modal-heading">
          <div>
            <span class="eyebrow">iLink Bot</span>
            <h2>绑定微信机器人</h2>
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
      <div class="modal-card modal-wide">
        <div class="modal-heading">
          <div>
            <span class="eyebrow">AI Provider</span>
            <h2>模型配置</h2>
          </div>
          <button class="modal-close" type="button" @click="modelModalOpen = false">关闭</button>
        </div>
        <div class="model-grid">
          <label class="field-label">
            <span>供应商名称</span>
            <input v-model="modelForm.provider_name" placeholder="ssh">
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
      <div class="modal-card">
        <div class="modal-heading">
          <div>
            <span class="eyebrow">账号名称</span>
            <h2>重命名账号</h2>
          </div>
          <button class="modal-close" type="button" @click="renameModalOpen = false">关闭</button>
        </div>
        <label class="field-label">
          <span>名称</span>
          <input v-model="renameValue" @keyup.enter="saveRenameAccount">
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
      <div class="modal-card">
        <div class="modal-heading">
          <div>
            <span class="eyebrow">删除账号</span>
            <h2>确认删除 {{ deleteAccountName }}</h2>
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
