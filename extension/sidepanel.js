const DEFAULT_SERVER_URL = "http://127.0.0.1:8787";
const TYPE_LABELS = {
  single_choice: "单选题",
  multiple_choice: "多选题",
  text: "文本题",
  unknown: "未知"
};

const elements = {
  tabLabel: document.querySelector("#tabLabel"),
  serverBadge: document.querySelector("#serverBadge"),
  serverUrl: document.querySelector("#serverUrl"),
  checkServer: document.querySelector("#checkServer"),
  status: document.querySelector("#status"),
  scanPage: document.querySelector("#scanPage"),
  getAnswer: document.querySelector("#getAnswer"),
  applyAnswer: document.querySelector("#applyAnswer"),
  nextQuestion: document.querySelector("#nextQuestion"),
  questionSection: document.querySelector("#questionSection"),
  questionTitle: document.querySelector("#questionTitle"),
  questionType: document.querySelector("#questionType"),
  questionNavigator: document.querySelector("#questionNavigator"),
  questionPicker: document.querySelector("#questionPicker"),
  questionCount: document.querySelector("#questionCount"),
  optionList: document.querySelector("#optionList"),
  answerSection: document.querySelector("#answerSection"),
  answerTitle: document.querySelector("#answerTitle"),
  confidenceBadge: document.querySelector("#confidenceBadge"),
  answerText: document.querySelector("#answerText"),
  answerExplanation: document.querySelector("#answerExplanation"),
  answerModel: document.querySelector("#answerModel"),
  discardAnswer: document.querySelector("#discardAnswer"),
  assignmentBadge: document.querySelector("#assignmentBadge"),
  syncAssignments: document.querySelector("#syncAssignments"),
  toggleAutoSync: document.querySelector("#toggleAutoSync"),
  assignmentSyncStatus: document.querySelector("#assignmentSyncStatus"),
  assignmentList: document.querySelector("#assignmentList"),
  rulesPanel: document.querySelector("#rulesPanel"),
  questionSelector: document.querySelector("#questionSelector"),
  stemSelector: document.querySelector("#stemSelector"),
  optionSelector: document.querySelector("#optionSelector"),
  nextSelector: document.querySelector("#nextSelector"),
  questionTypeMode: document.querySelector("#questionTypeMode"),
  saveRules: document.querySelector("#saveRules")
};

const state = {
  tab: null,
  origin: "",
  config: {},
  health: null,
  questions: [],
  answers: [],
  appliedQuestions: new Set(),
  activeQuestionIndex: 0,
  question: null,
  answer: null,
  applied: false,
  autoAssignmentSync: false,
  syncPolling: false
};

function normalizeServerUrl(value) {
  const trimmed = String(value || "").trim().replace(/\/+$/, "");
  if (!trimmed) {
    return DEFAULT_SERVER_URL;
  }
  return /^https?:\/\//i.test(trimmed) ? trimmed : `http://${trimmed}`;
}

function setStatus(message, tone = "neutral") {
  elements.status.textContent = message;
  elements.status.className = `status status-${tone}`;
}

function setBusy(button, busy, busyText = "处理中") {
  if (!button) {
    return;
  }
  if (busy) {
    button.dataset.previousText = button.textContent;
    button.textContent = busyText;
    button.disabled = true;
  } else {
    button.textContent = button.dataset.previousText || button.textContent;
    delete button.dataset.previousText;
    button.disabled = false;
  }
  updateActionState();
}

function updateActionState() {
  const count = state.questions.length;
  const hasQuestions = count > 0;
  const hasAnyAnswer = state.answers.some(Boolean);
  const isMultiQuestion = count > 1;

  if (!elements.getAnswer.dataset.previousText) {
    elements.getAnswer.textContent = isMultiQuestion ? "获取全部答案" : "获取答案";
  }
  if (!elements.applyAnswer.dataset.previousText) {
    elements.applyAnswer.textContent = isMultiQuestion ? "填入全部答案" : "填入答案";
  }

  elements.getAnswer.disabled = !hasQuestions || Boolean(elements.getAnswer.dataset.previousText);
  elements.applyAnswer.disabled = !hasAnyAnswer || Boolean(elements.applyAnswer.dataset.previousText);
  elements.nextQuestion.hidden = isMultiQuestion;
  elements.nextQuestion.disabled = isMultiQuestion || !state.applied || Boolean(elements.nextQuestion.dataset.previousText);
}

function getServerUrl() {
  return normalizeServerUrl(elements.serverUrl.value);
}

async function getActiveTab() {
  const queryResults = await Promise.all([
    chrome.tabs.query({ active: true, currentWindow: true }),
    chrome.tabs.query({ active: true, lastFocusedWindow: true })
  ]);
  const candidates = queryResults.flat().filter((tab) => Boolean(tab?.id));

  if (candidates.length === 0) {
    throw new Error("没有找到当前标签页");
  }

  for (const tab of candidates) {
    const address = tab.url || tab.pendingUrl;
    if (!address) {
      continue;
    }
    const url = new URL(address);
    if (["http:", "https:"].includes(url.protocol)) {
      return { tab, url };
    }
  }

  throw new Error("请先打开目标答题网页，再点击扩展图标");
}

function getOriginPattern(url) {
  return `${url.protocol}//${url.host}/*`;
}

async function requestOriginPermission(url) {
  const originPattern = getOriginPattern(url);
  const hasPermission = await chrome.permissions.contains({ origins: [originPattern] });
  if (hasPermission) {
    return;
  }
  const granted = await chrome.permissions.request({ origins: [originPattern] });
  if (!granted) {
    throw new Error("未获得当前网站权限");
  }
}

async function ensureContentScript(tabId) {
  await chrome.scripting.executeScript({
    target: { tabId },
    files: ["content.js"]
  });
}

async function sendToPage(message) {
  const tab = state.tab;
  if (!tab) {
    throw new Error("请先扫描当前页面");
  }

  await ensureContentScript(tab.id);
  const response = await chrome.tabs.sendMessage(tab.id, message);
  if (!response?.ok) {
    throw new Error(response?.error || "页面操作失败");
  }
  return response;
}

function readRules() {
  return {
    questionSelector: elements.questionSelector.value.trim(),
    stemSelector: elements.stemSelector.value.trim(),
    optionSelector: elements.optionSelector.value.trim(),
    nextSelector: elements.nextSelector.value.trim(),
    type: elements.questionTypeMode.value
  };
}

function writeRules(config = {}) {
  elements.questionSelector.value = config.questionSelector || "";
  elements.stemSelector.value = config.stemSelector || "";
  elements.optionSelector.value = config.optionSelector || "";
  elements.nextSelector.value = config.nextSelector || "";
  elements.questionTypeMode.value = config.type || "";
}

function rulesStorageKey(origin) {
  return `siteRules:${origin}`;
}

async function saveRules(silent = false) {
  const { tab, url } = await getActiveTab();
  state.tab = tab;
  state.origin = url.origin;
  state.config = readRules();
  await chrome.storage.local.set({
    [rulesStorageKey(url.origin)]: state.config
  });
  if (!silent) {
    setStatus("当前站点规则已保存", "success");
  }
}

async function loadSiteContext() {
  try {
    const { tab, url } = await getActiveTab();
    state.tab = tab;
    state.origin = url.origin;
    elements.tabLabel.textContent = `${url.hostname}${url.pathname === "/" ? "" : url.pathname}`;
    elements.tabLabel.title = tab.url || tab.pendingUrl || "";
    const key = rulesStorageKey(url.origin);
    const stored = await chrome.storage.local.get(key);
    writeRules(stored[key] || {});
    return { tab, url };
  } catch (error) {
    elements.tabLabel.textContent = "当前页面不可用";
    setStatus(error.message, "warning");
    return null;
  }
}

async function checkServer() {
  const serverUrl = getServerUrl();
  elements.serverUrl.value = serverUrl;
  await chrome.storage.local.set({ serverUrl });
  setBusy(elements.checkServer, true, "连接中");
  try {
    const response = await fetch(`${serverUrl}/api/health`, {
      method: "GET",
      cache: "no-store"
    });
    if (!response.ok) {
      throw new Error(`服务返回 ${response.status}`);
    }
    const health = await response.json();
    state.health = health;
    elements.serverBadge.textContent = health.mode === "live" ? "AI 已连接" : "演示模式";
    elements.serverBadge.className = `badge ${health.mode === "live" ? "badge-live" : "badge-mock"}`;
    const reasoning = health.reasoningEffort
      ? `，思考强度：${health.reasoningEffort}`
      : "";
    setStatus(
      health.mode === "live"
        ? `本地服务已连接，模型：${health.model}${reasoning}`
        : "本地服务已连接，目前返回演示答案",
      health.mode === "live" ? "success" : "warning"
    );
    return health;
  } catch (error) {
    state.health = null;
    elements.serverBadge.textContent = "服务离线";
    elements.serverBadge.className = "badge badge-low";
    setStatus(`无法连接本地服务：${error.message}`, "error");
    return null;
  } finally {
    setBusy(elements.checkServer, false);
  }
}

function formatSyncTime(value) {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit"
      });
}

async function requestChaoxingBatchPermission() {
  const origins = ["https://*.chaoxing.com/*"];
  const hasPermission = await chrome.permissions.contains({ origins });
  if (hasPermission) {
    return;
  }
  const granted = await chrome.permissions.request({ origins });
  if (!granted) {
    throw new Error("未获得 Chaoxing 域名权限");
  }
}

function renderAssignmentSummary(summary) {
  elements.assignmentList.replaceChildren();
  const count = summary?.unfinishedCount || 0;
  if (!summary?.syncedAt) {
    elements.assignmentBadge.textContent = "未同步";
    elements.assignmentBadge.className = "badge badge-neutral";
    elements.assignmentSyncStatus.textContent = "尚未同步";
    return;
  }

  elements.assignmentBadge.textContent = `${count} 个未完成`;
  elements.assignmentBadge.className = count > 0
    ? "badge badge-medium"
    : "badge badge-live";
  elements.assignmentSyncStatus.textContent = [
    `同步于 ${formatSyncTime(summary.syncedAt)}`,
    `扫描 ${summary.coursesScanned || summary.courses?.length || 0} 门课程`
  ].join(" · ");

  const courses = [...(summary.courses || [])]
    .filter((course) => course.assignments?.length)
    .sort((a, b) => String(b.startDate).localeCompare(String(a.startDate)));
  for (const course of courses) {
    const section = document.createElement("section");
    section.className = "assignment-course";
    const heading = document.createElement("h3");
    heading.textContent = `${course.courseName} · ${course.assignments.length}`;
    const list = document.createElement("ul");
    for (const assignment of course.assignments) {
      const item = document.createElement("li");
      const link = document.createElement("a");
      link.textContent = assignment.title;
      link.href = assignment.taskUrl;
      link.target = "_blank";
      link.rel = "noreferrer";
      const meta = document.createElement("span");
      meta.className = "assignment-time";
      meta.textContent = assignment.timeText || "未显示截止时间";
      item.append(link, meta);
      list.append(item);
    }
    section.append(heading, list);
    elements.assignmentList.append(section);
  }
}

async function loadAssignmentSummary() {
  try {
    const response = await fetch(
      `${getServerUrl()}/api/assignments/summary?academicYear=2026&status=unfinished&submittable=true`,
      { cache: "no-store" }
    );
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      throw new Error(payload.error || "读取作业汇总失败");
    }
    renderAssignmentSummary(payload);
    return payload;
  } catch (error) {
    elements.assignmentBadge.textContent = "读取失败";
    elements.assignmentBadge.className = "badge badge-low";
    elements.assignmentSyncStatus.textContent = error.message;
    return null;
  }
}

async function pollAssignmentSync() {
  if (state.syncPolling) {
    return;
  }
  state.syncPolling = true;
  elements.syncAssignments.disabled = true;
  elements.syncAssignments.textContent = "同步中...";
  try {
    for (let attempt = 0; attempt < 180; attempt += 1) {
      const stored = await chrome.storage.local.get(["assignmentSyncState"]);
      const syncState = stored.assignmentSyncState;
      if (syncState?.message) {
        elements.assignmentSyncStatus.textContent = syncState.message;
      }
      if (syncState?.status === "success") {
        await loadAssignmentSummary();
        setStatus(syncState.message || "作业同步完成", "success");
        return;
      }
      if (syncState?.status === "error") {
        setStatus(syncState.message || "作业同步失败", "error");
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 1200));
    }
    throw new Error("作业同步等待超时");
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    state.syncPolling = false;
    elements.syncAssignments.disabled = false;
    elements.syncAssignments.textContent = "同步未完成作业";
  }
}

async function startAssignmentSync() {
  try {
    await requestChaoxingBatchPermission();
    await chrome.storage.local.set({
      assignmentSyncState: {
        status: "starting",
        message: "正在启动作业同步...",
        updatedAt: new Date().toISOString()
      }
    });
    const response = await chrome.runtime.sendMessage({
      type: "START_ASSIGNMENT_SYNC",
      academicYear: 2026
    });
    if (!response?.ok) {
      throw new Error(response?.error || "无法启动作业同步");
    }
    pollAssignmentSync();
  } catch (error) {
    setStatus(error.message, "error");
  }
}

function renderAutoSyncState() {
  elements.toggleAutoSync.textContent = state.autoAssignmentSync
    ? "自动检测：每 6 小时"
    : "自动检测：关闭";
}

async function loadAutoSyncState() {
  const stored = await chrome.storage.local.get(["autoAssignmentSync"]);
  state.autoAssignmentSync = Boolean(stored.autoAssignmentSync);
  renderAutoSyncState();
}

async function toggleAutoSync() {
  try {
    if (!state.autoAssignmentSync) {
      await requestChaoxingBatchPermission();
    }
    const enabled = !state.autoAssignmentSync;
    const response = await chrome.runtime.sendMessage({
      type: "CONFIGURE_ASSIGNMENT_ALARM",
      enabled,
      periodMinutes: 360
    });
    if (!response?.ok) {
      throw new Error(response?.error || "自动检测设置失败");
    }
    state.autoAssignmentSync = enabled;
    renderAutoSyncState();
    setStatus(enabled ? "已开启每 6 小时自动检测" : "已关闭自动检测", "success");
  } catch (error) {
    setStatus(error.message, "error");
  }
}

function renderQuestionNavigator() {
  const count = state.questions.length;
  elements.questionNavigator.hidden = count <= 1;
  elements.questionPicker.replaceChildren();
  if (count <= 1) {
    elements.questionCount.textContent = "";
    return;
  }

  state.questions.forEach((question, index) => {
    const option = document.createElement("option");
    option.value = String(index);
    const summary = question.stem.replace(/\s+/g, " ").slice(0, 28);
    option.textContent = `${index + 1}. ${summary}${question.stem.length > 28 ? "…" : ""}`;
    elements.questionPicker.append(option);
  });
  elements.questionPicker.value = String(state.activeQuestionIndex);
  elements.questionCount.textContent = `共 ${count} 道`;
}

function selectQuestion(index) {
  if (index < 0 || index >= state.questions.length) {
    return;
  }
  state.activeQuestionIndex = index;
  state.question = state.questions[index];
  state.answer = state.answers[index] || null;
  elements.questionPicker.value = String(index);
  renderQuestion(state.question);
  if (state.answer) {
    renderAnswer(state.answer);
  } else {
    elements.answerSection.hidden = true;
  }
  updateActionState();
}

function renderQuestion(question) {
  if (!question) {
    return;
  }
  elements.questionTitle.textContent = question.stem;
  elements.questionType.textContent = TYPE_LABELS[question.type] || "未知";
  renderQuestionNavigator();
  elements.optionList.replaceChildren();

  const selected = new Set(state.answer?.selectedIndices || []);
  for (const option of question.options) {
    const item = document.createElement("li");
    if (selected.has(option.index)) {
      item.classList.add("is-selected");
    }
    const index = document.createElement("span");
    index.className = "option-index";
    index.textContent = String.fromCharCode(65 + option.index);
    const text = document.createElement("span");
    text.textContent = option.text;
    item.append(index, text);
    elements.optionList.append(item);
  }

  elements.optionList.hidden = question.options.length === 0;
  elements.questionSection.hidden = false;

  if (state.questions.length > 1) {
    elements.questionTitle.title = `第 ${state.activeQuestionIndex + 1} 道题`;
  } else {
    elements.questionTitle.title = "";
  }
}

function confidenceTone(confidence) {
  if (confidence >= 0.75) {
    return "high";
  }
  if (confidence >= 0.45) {
    return "medium";
  }
  return "low";
}

function renderAnswer(answer) {
  const selectedOptions = (answer.selectedIndices || [])
    .map((index) => state.question.options.find((option) => option.index === index))
    .filter(Boolean);
  elements.answerTitle.textContent = answer.type === "text"
    ? "文本建议"
    : selectedOptions.length > 0
      ? selectedOptions.map((option) => String.fromCharCode(65 + option.index)).join("、")
      : "未生成答案";
  elements.answerText.textContent = answer.type === "text"
    ? answer.textAnswer
    : selectedOptions.map((option) => `${String.fromCharCode(65 + option.index)}. ${option.text}`).join("\n");
  elements.answerExplanation.textContent = answer.explanation || "";
  elements.answerExplanation.hidden = !answer.explanation;
  const percentage = Math.round((answer.confidence || 0) * 100);
  elements.confidenceBadge.textContent = `置信度 ${percentage}%`;
  elements.confidenceBadge.className = `badge badge-${confidenceTone(answer.confidence)}`;
  elements.answerModel.textContent = answer.mode === "mock"
    ? "本地演示模式"
    : [
        answer.model || "AI",
        answer.reasoningEffort ? `思考 ${answer.reasoningEffort}` : "",
        answer.mode || "live"
      ].filter(Boolean).join(" · ");
  elements.answerSection.hidden = false;
  renderQuestion(state.question);
  updateActionState();
}

function clearCurrentAnswer() {
  if (state.activeQuestionIndex >= 0) {
    state.answers[state.activeQuestionIndex] = null;
    state.appliedQuestions.delete(state.activeQuestionIndex);
  }
  state.answer = null;
  state.applied = false;
  elements.answerSection.hidden = true;
  elements.answerTitle.textContent = "等待生成";
  elements.answerText.textContent = "";
  elements.answerExplanation.textContent = "";
  elements.answerModel.textContent = "";
  if (state.question) {
    renderQuestion(state.question);
  }
  updateActionState();
}

function clearAllAnswers() {
  state.answers = state.questions.map(() => null);
  state.appliedQuestions.clear();
  state.answer = null;
  state.applied = false;
  elements.answerSection.hidden = true;
  elements.answerTitle.textContent = "等待生成";
  elements.answerText.textContent = "";
  elements.answerExplanation.textContent = "";
  elements.answerModel.textContent = "";
  updateActionState();
}

function resetWorkspace() {
  state.questions = [];
  state.answers = [];
  state.appliedQuestions.clear();
  state.activeQuestionIndex = 0;
  state.question = null;
  state.answer = null;
  state.applied = false;
  elements.questionSection.hidden = true;
  elements.answerSection.hidden = true;
  elements.questionNavigator.hidden = true;
  elements.scanPage.textContent = "授权并扫描";
  updateActionState();
}

async function scanPage() {
  setBusy(elements.scanPage, true, "扫描中");
  try {
    const context = await getActiveTab();
    state.tab = context.tab;
    state.origin = context.url.origin;
    await requestOriginPermission(context.url);
    writeRules(readRules());
    await saveRules(true);
    const response = await sendToPage({
      type: "CAPTURE_QUESTIONS",
      config: state.config
    });
    state.questions = response.questions || [];
    state.answers = state.questions.map(() => null);
    state.appliedQuestions.clear();
    state.activeQuestionIndex = 0;
    state.question = state.questions[0] || null;
    state.answer = null;
    state.applied = false;
    if (!state.question) {
      throw new Error("没有识别到题目");
    }
    elements.answerSection.hidden = true;
    renderQuestion(state.question);
    setStatus(
      state.questions.length > 1
        ? `已识别 ${state.questions.length} 道题，可批量生成和填入`
        : `已识别${TYPE_LABELS[state.question.type] || "题目"}，共 ${state.question.options.length} 个选项`,
      "success"
    );
  } catch (error) {
    resetWorkspace();
    elements.scanPage.textContent = "重新扫描";
    setStatus(error.message, "error");
  } finally {
    setBusy(elements.scanPage, false);
    elements.scanPage.textContent = "重新扫描";
    updateActionState();
  }
}

async function requestAnswerForQuestion(question) {
  const serverUrl = getServerUrl();
  const response = await fetch(`${serverUrl}/api/solve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question })
  });
  const payload = await response.json();
  if (!response.ok || !payload.ok) {
    throw new Error(payload.error || "获取答案失败");
  }
  return payload.answer;
}

async function getAnswer() {
  if (state.questions.length === 0) {
    return;
  }
  const count = state.questions.length;
  setBusy(elements.getAnswer, true, count > 1 ? `生成 1/${count}` : "生成中");
  try {
    const answers = new Array(count).fill(null);
    const errors = [];
    for (let index = 0; index < state.questions.length; index += 1) {
      elements.getAnswer.textContent = count > 1
        ? `生成 ${index + 1}/${count}`
        : "生成中";
      try {
        answers[index] = await requestAnswerForQuestion(state.questions[index]);
      } catch (error) {
        errors.push(`第 ${index + 1} 题：${error.message}`);
      }
    }

    state.answers = answers;
    state.appliedQuestions.clear();
    state.applied = false;
    const firstAnsweredIndex = answers.findIndex(Boolean);
    if (firstAnsweredIndex === -1) {
      throw new Error(errors.join("；") || "没有生成答案");
    }
    selectQuestion(firstAnsweredIndex);

    const generatedCount = answers.filter(Boolean).length;
    const mockMode = answers.some((answer) => answer?.mode === "mock");
    if (errors.length > 0) {
      setStatus(`已生成 ${generatedCount}/${count} 道，${errors.join("；")}`, "warning");
    } else if (mockMode) {
      setStatus("已生成演示答案，请配置 AI Key 后再用于实际答题", "warning");
    } else {
      setStatus(`已生成 ${generatedCount} 道答案，请确认后填入页面`, "success");
    }
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    setBusy(elements.getAnswer, false);
    updateActionState();
  }
}

async function fillUEditorInMainWorld(payload) {
  const container = document.getElementById(payload.containerId);
  if (!container) {
    return { applied: false, error: "页面中找不到对应题目容器" };
  }

  const iframe = document.getElementById(payload.iframeId)
    || container.querySelector("iframe[id^='ueditor_']");
  const hiddenInput = document.getElementById(payload.hiddenInputId)
    || container.querySelector("textarea[id^='answer'], textarea[name^='answer']");
  const escapeHtml = (value) => String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
  const html = String(payload.text || "")
    .split(/\r?\n/)
    .map((line) => line ? `<p>${escapeHtml(line)}</p>` : "<p><br></p>")
    .join("");

  let editor = null;
  const instances = globalThis.UE?.instants || {};
  for (const [key, candidate] of Object.entries(instances)) {
    if (
      key === payload.instanceKey
      || candidate?.iframe?.id === payload.iframeId
      || candidate?.container?.closest?.("[id^='question']")?.id === payload.containerId
    ) {
      editor = candidate;
      break;
    }
  }

  try {
    if (editor?.setContent) {
      editor.setContent(html, false);
      editor.sync?.();
    } else if (iframe?.contentDocument?.body) {
      iframe.contentDocument.body.innerHTML = html;
    } else {
      return { applied: false, error: "富文本编辑区域尚未就绪" };
    }

    if (hiddenInput) {
      hiddenInput.value = payload.text;
      hiddenInput.dispatchEvent(new Event("input", { bubbles: true }));
      hiddenInput.dispatchEvent(new Event("change", { bubbles: true }));
    }

    const body = iframe?.contentDocument?.body;
    body?.dispatchEvent(new InputEvent("input", {
      bubbles: true,
      inputType: "insertText",
      data: payload.text
    }));
    await new Promise((resolve) => setTimeout(resolve, 80));

    const verifiedText = editor?.getContentTxt?.()
      || body?.innerText
      || hiddenInput?.value
      || "";
    return {
      applied: Boolean(String(verifiedText).trim()),
      method: editor ? "ueditor-api" : "iframe-dom",
      verifiedLength: String(verifiedText).trim().length
    };
  } catch (error) {
    return {
      applied: false,
      error: error instanceof Error ? error.message : "富文本写入失败"
    };
  }
}

async function applyUEditorAnswer(question, answer) {
  if (!state.tab) {
    throw new Error("没有可操作的目标页面");
  }
  const [injectionResult] = await chrome.scripting.executeScript({
    target: { tabId: state.tab.id },
    world: "MAIN",
    func: fillUEditorInMainWorld,
    args: [{
      containerId: question.containerId,
      iframeId: question.editor.iframeId,
      hiddenInputId: question.editor.hiddenInputId,
      instanceKey: question.editor.instanceKey,
      text: answer.textAnswer || ""
    }]
  });
  const result = injectionResult?.result;
  if (!result?.applied) {
    throw new Error(result?.error || "富文本答案填入失败");
  }
  return result;
}

async function applyAnswerToQuestion(question, answer) {
  if (answer.type === "text" && question.editor?.kind === "ueditor") {
    return applyUEditorAnswer(question, answer);
  }
  const response = await sendToPage({
    type: "APPLY_ANSWER",
    question,
    answer,
    config: state.config
  });
  return response.result;
}

async function applyAnswer() {
  if (state.questions.length === 0 || !state.answers.some(Boolean)) {
    return;
  }
  const count = state.questions.length;
  setBusy(elements.applyAnswer, true, count > 1 ? `填入 1/${count}` : "填入中");
  try {
    const errors = [];
    for (let index = 0; index < state.questions.length; index += 1) {
      const answer = state.answers[index];
      if (!answer) {
        continue;
      }
      elements.applyAnswer.textContent = count > 1
        ? `填入 ${index + 1}/${count}`
        : "填入中";
      try {
        await applyAnswerToQuestion(state.questions[index], answer);
        state.appliedQuestions.add(index);
      } catch (error) {
        errors.push(`第 ${index + 1} 题：${error.message}`);
      }
    }

    state.applied = state.appliedQuestions.size > 0;
    if (errors.length > 0) {
      setStatus(
        `已填入 ${state.appliedQuestions.size}/${state.answers.filter(Boolean).length} 道，${errors.join("；")}`,
        "warning"
      );
    } else {
      setStatus(`已填入 ${state.appliedQuestions.size} 道答案，提交前请人工核对`, "success");
    }
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    setBusy(elements.applyAnswer, false);
    updateActionState();
  }
}

async function goNext() {
  setBusy(elements.nextQuestion, true, "切换中");
  try {
    await sendToPage({
      type: "CLICK_NEXT",
      config: state.config
    });
    await new Promise((resolve) => setTimeout(resolve, 700));
    await scanPage();
    setStatus("已进入下一题", "success");
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    setBusy(elements.nextQuestion, false);
    updateActionState();
  }
}

async function init() {
  const stored = await chrome.storage.local.get([
    "serverUrl",
    "assignmentSyncState"
  ]);
  elements.serverUrl.value = stored.serverUrl || DEFAULT_SERVER_URL;
  await loadSiteContext();
  await checkServer();
  await Promise.all([
    loadAssignmentSummary(),
    loadAutoSyncState()
  ]);
  if (stored.assignmentSyncState?.status === "running") {
    pollAssignmentSync();
  }
}

elements.checkServer.addEventListener("click", checkServer);
elements.scanPage.addEventListener("click", scanPage);
elements.getAnswer.addEventListener("click", getAnswer);
elements.applyAnswer.addEventListener("click", applyAnswer);
elements.nextQuestion.addEventListener("click", goNext);
elements.discardAnswer.addEventListener("click", clearCurrentAnswer);
elements.syncAssignments.addEventListener("click", startAssignmentSync);
elements.toggleAutoSync.addEventListener("click", toggleAutoSync);
elements.questionPicker.addEventListener("change", () => {
  selectQuestion(Number(elements.questionPicker.value));
});
elements.saveRules.addEventListener("click", () => saveRules(false));
elements.serverUrl.addEventListener("change", () => {
  elements.serverUrl.value = getServerUrl();
  checkServer();
});

chrome.tabs.onActivated.addListener(() => {
  resetWorkspace();
  loadSiteContext();
  updateActionState();
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (changeInfo.status === "complete" && state.tab?.id === tabId) {
    resetWorkspace();
    loadSiteContext();
    updateActionState();
  }
});

init();
