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
  optionList: document.querySelector("#optionList"),
  answerSection: document.querySelector("#answerSection"),
  answerTitle: document.querySelector("#answerTitle"),
  confidenceBadge: document.querySelector("#confidenceBadge"),
  answerText: document.querySelector("#answerText"),
  answerExplanation: document.querySelector("#answerExplanation"),
  answerModel: document.querySelector("#answerModel"),
  discardAnswer: document.querySelector("#discardAnswer"),
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
  question: null,
  answer: null,
  applied: false
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
  const hasQuestion = Boolean(state.question);
  const hasAnswer = Boolean(state.answer);
  elements.getAnswer.disabled = !hasQuestion || Boolean(elements.getAnswer.dataset.previousText);
  elements.applyAnswer.disabled = !hasQuestion || !hasAnswer || Boolean(elements.applyAnswer.dataset.previousText);
  elements.nextQuestion.disabled = !state.applied || Boolean(elements.nextQuestion.dataset.previousText);
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

function renderQuestion(question) {
  elements.questionTitle.textContent = question.stem;
  elements.questionType.textContent = TYPE_LABELS[question.type] || "未知";
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

function clearAnswer() {
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

function resetWorkspace() {
  state.question = null;
  state.answer = null;
  state.applied = false;
  elements.questionSection.hidden = true;
  elements.answerSection.hidden = true;
  elements.scanPage.textContent = "授权并扫描";
  clearAnswer();
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
      type: "CAPTURE_QUESTION",
      config: state.config
    });
    state.question = response.question;
    clearAnswer();
    renderQuestion(state.question);
    setStatus(`已识别${TYPE_LABELS[state.question.type] || "题目"}，共 ${state.question.options.length} 个选项`, "success");
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

async function getAnswer() {
  if (!state.question) {
    return;
  }
  setBusy(elements.getAnswer, true, "生成中");
  try {
    const serverUrl = getServerUrl();
    const response = await fetch(`${serverUrl}/api/solve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: state.question })
    });
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      throw new Error(payload.error || "获取答案失败");
    }
    state.answer = payload.answer;
    state.applied = false;
    renderAnswer(payload.answer);
    setStatus(
      payload.answer.mode === "mock"
        ? "已生成演示答案，请配置 AI Key 后再用于实际答题"
        : "答案已生成，请确认后填入页面",
      payload.answer.mode === "mock" ? "warning" : "success"
    );
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    setBusy(elements.getAnswer, false);
    updateActionState();
  }
}

async function applyAnswer() {
  if (!state.question || !state.answer) {
    return;
  }
  setBusy(elements.applyAnswer, true, "填入中");
  try {
    await sendToPage({
      type: "APPLY_ANSWER",
      question: state.question,
      answer: state.answer,
      config: state.config
    });
    state.applied = true;
    setStatus("答案已填入，提交前请人工核对", "success");
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
  const stored = await chrome.storage.local.get(["serverUrl"]);
  elements.serverUrl.value = stored.serverUrl || DEFAULT_SERVER_URL;
  await loadSiteContext();
  await checkServer();
}

elements.checkServer.addEventListener("click", checkServer);
elements.scanPage.addEventListener("click", scanPage);
elements.getAnswer.addEventListener("click", getAnswer);
elements.applyAnswer.addEventListener("click", applyAnswer);
elements.nextQuestion.addEventListener("click", goNext);
elements.discardAnswer.addEventListener("click", clearAnswer);
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
