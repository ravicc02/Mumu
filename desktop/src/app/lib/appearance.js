export const APPEARANCE_KEY = "studio:appearance";
export const THEME_MODES = ["system", "light", "dark"];

export function loadAppearance(storage = globalThis.localStorage) {
  try {
    const value = JSON.parse(storage.getItem(APPEARANCE_KEY) || "null") || {};
    return {
      theme: THEME_MODES.includes(value.theme) ? value.theme : "system",
      reduceMotion: value.reduceMotion === true,
    };
  } catch {
    return { theme: "system", reduceMotion: false };
  }
}

export function saveAppearance(value, storage = globalThis.localStorage) {
  try { storage.setItem(APPEARANCE_KEY, JSON.stringify(value)); } catch { /* 偏好只在当前窗口生效。 */ }
}

export function resolveTheme(theme, prefersDark) {
  return theme === "dark" || (theme === "system" && prefersDark) ? "dark" : "light";
}

/** 写入 html 的主题类；切换瞬间暂停过渡，避免带 transition 的属性保留旧主题的值。 */
export function applyAppearance(value, root = document.documentElement, prefersDark = globalThis.matchMedia?.("(prefers-color-scheme: dark)").matches === true) {
  const dark = resolveTheme(value.theme, prefersDark) === "dark";
  const changed = root.classList.contains("dark") !== dark;
  if (changed) root.classList.add("ds-theme-switching");
  root.classList.toggle("dark", dark);
  root.classList.toggle("reduce-motion", value.reduceMotion === true);
  if (changed) {
    root.getBoundingClientRect();
    requestAnimationFrame(() => requestAnimationFrame(() => root.classList.remove("ds-theme-switching")));
  }
  return dark ? "dark" : "light";
}

export function watchSystemTheme(getValue, root = document.documentElement) {
  const query = globalThis.matchMedia?.("(prefers-color-scheme: dark)");
  if (!query) return () => {};
  const listener = () => { if (getValue().theme === "system") applyAppearance(getValue(), root, query.matches); };
  query.addEventListener("change", listener);
  return () => query.removeEventListener("change", listener);
}
