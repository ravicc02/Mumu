async function enableActionClick() {
  try {
    await chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
  } catch (error) {
    console.warn("Unable to configure side panel behavior", error);
  }
}

const ASSIGNMENT_ALARM = "chaoxing-assignment-sync";
const COLLECTOR_PAGE = "collector.html";
const CHAXING_PERMISSION = "https://*.chaoxing.com/*";

async function configureAssignmentAlarm() {
  const stored = await chrome.storage.local.get([
    "autoAssignmentSync",
    "assignmentSyncPeriodMinutes"
  ]);
  await chrome.alarms.clear(ASSIGNMENT_ALARM);
  if (!stored.autoAssignmentSync) {
    return;
  }
  const periodInMinutes = Math.max(
    30,
    Number(stored.assignmentSyncPeriodMinutes) || 360
  );
  await chrome.alarms.create(ASSIGNMENT_ALARM, { periodInMinutes });
}

async function closeCollectorDocument() {
  try {
    await chrome.offscreen.closeDocument();
  } catch {
    // Closing a document that does not exist is safe to ignore.
  }
}

async function startHeadlessAssignmentSync(mode = "manual", academicYear = 2026) {
  const hasPermission = await chrome.permissions.contains({
    origins: [CHAXING_PERMISSION]
  });
  if (!hasPermission) {
    throw new Error("请先授权 Chaoxing 域名");
  }

  await closeCollectorDocument();
  const request = {
    mode,
    academicYear,
    requestedAt: new Date().toISOString()
  };
  await chrome.storage.local.set({
    assignmentSyncRequest: request,
    assignmentSyncState: {
      status: "starting",
      mode,
      academicYear,
      message: "正在启动无头同步...",
      updatedAt: new Date().toISOString()
    }
  });
  await chrome.offscreen.createDocument({
    url: COLLECTOR_PAGE,
    reasons: ["DOM_PARSER"],
    justification: "Fetch and parse Chaoxing course and assignment HTML."
  });
  return request;
}

async function initializeBackground() {
  await enableActionClick();
  await configureAssignmentAlarm();
}

chrome.runtime.onInstalled.addListener(initializeBackground);
chrome.runtime.onStartup.addListener(initializeBackground);

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === "START_ASSIGNMENT_SYNC") {
    startHeadlessAssignmentSync("manual", Number(message.academicYear) || 2026)
      .then(() => sendResponse({ ok: true, headless: true }))
      .catch((error) => sendResponse({ ok: false, error: error.message }));
    return true;
  }
  if (message?.type === "CONFIGURE_ASSIGNMENT_ALARM") {
    chrome.storage.local.set({
      autoAssignmentSync: Boolean(message.enabled),
      assignmentSyncPeriodMinutes: Math.max(
        30,
        Number(message.periodMinutes) || 360
      )
    }).then(configureAssignmentAlarm)
      .then(() => sendResponse({ ok: true }))
      .catch((error) => sendResponse({ ok: false, error: error.message }));
    return true;
  }
  if (message?.type === "GET_ASSIGNMENT_SYNC_CONFIG") {
    chrome.storage.local.get([
      "assignmentSyncRequest",
      "serverUrl"
    ]).then((stored) => sendResponse({
      ok: true,
      request: stored.assignmentSyncRequest,
      serverUrl: stored.serverUrl
    })).catch((error) => sendResponse({ ok: false, error: error.message }));
    return true;
  }
  if (message?.type === "UPDATE_ASSIGNMENT_SYNC_STATE") {
    chrome.storage.local.set({
      assignmentSyncState: message.state
    }).then(() => sendResponse({ ok: true }))
      .catch((error) => sendResponse({ ok: false, error: error.message }));
    return true;
  }
  if (message?.type === "ASSIGNMENT_SYNC_FINISHED") {
    const count = Number(message.submittableCount) || 0;
    Promise.all([
      chrome.action.setBadgeBackgroundColor({ color: "#176b45" }),
      chrome.action.setBadgeText({ text: count > 0 ? String(count) : "" })
    ]).then(() => {
      sendResponse({ ok: true });
      closeCollectorDocument();
    })
      .catch((error) => sendResponse({ ok: false, error: error.message }));
    return true;
  }
  return false;
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === ASSIGNMENT_ALARM) {
    startHeadlessAssignmentSync("scheduled", 2026).catch((error) => {
      console.warn("Scheduled assignment sync failed to start", error);
    });
  }
});

enableActionClick();
