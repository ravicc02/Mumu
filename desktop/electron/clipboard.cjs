function registerClipboard(ipcMain, clipboard, getMainWindow) {
  ipcMain.handle("app:write-clipboard-text", (event, value) => {
    const window = getMainWindow();
    if (!window || event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame) {
      throw new Error("复制操作来源无效");
    }
    if (typeof value !== "string" || value.length > 1_000_000) throw new Error("复制内容无效或过长");
    clipboard.writeText(value);
    return true;
  });
}
module.exports = { registerClipboard };
