async function enableActionClick() {
  try {
    await chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
  } catch (error) {
    console.warn("Unable to configure side panel behavior", error);
  }
}

chrome.runtime.onInstalled.addListener(enableActionClick);
chrome.runtime.onStartup.addListener(enableActionClick);
enableActionClick();
