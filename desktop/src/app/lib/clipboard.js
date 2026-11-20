export async function copyText(value, options = {}) {
  if (typeof value !== "string") throw new TypeError("复制内容必须为文本。");
  const desktop = options.desktop ?? globalThis.window?.desktopApi;
  const navigator = options.navigator ?? globalThis.navigator;
  const document = options.document ?? globalThis.document;
  if (desktop?.writeClipboardText) {
    try { await desktop.writeClipboardText(value); return; } catch { /* 继续尝试浏览器通道。 */ }
  }
  if (navigator?.clipboard?.writeText) {
    try { await navigator.clipboard.writeText(value); return; } catch { /* 非安全上下文或权限拒绝时使用兼容通道。 */ }
  }
  if (!document?.body || !document.execCommand) throw new Error("无法复制，请选中文本后手动复制。");
  const active = document.activeElement;
  const selection = document.getSelection?.();
  const ranges = selection ? Array.from({ length: selection.rangeCount }, (_, index) => selection.getRangeAt(index).cloneRange()) : [];
  const fieldSelection = active && typeof active.selectionStart === "number"
    ? [active.selectionStart, active.selectionEnd, active.selectionDirection] : null;
  const field = document.createElement("textarea");
  field.value = value;
  field.readOnly = true;
  field.setAttribute("aria-label", "复制内容");
  Object.assign(field.style, { position: "fixed", left: "-9999px", top: "0", opacity: "0" });
  document.body.appendChild(field);
  try {
    field.focus({ preventScroll: true });
    field.select();
    if (!document.execCommand("copy")) throw new Error("无法复制，请选中文本后手动复制。");
  } finally {
    field.remove();
    active?.focus?.({ preventScroll: true });
    if (selection) {
      selection.removeAllRanges();
      for (const range of ranges) selection.addRange(range);
    }
    if (fieldSelection) active.setSelectionRange?.(...fieldSelection);
  }
}
