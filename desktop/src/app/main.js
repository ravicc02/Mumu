import { createApp } from "vue";
import App from "./App.vue";
import "../styles/tokens.css";
import "../styles/workbench.css";
import "../styles/mascot.css";
import "../styles/settings.css";
import "../styles/design-system.css";
import "../styles/polish.css";
import { applyAppearance, loadAppearance, watchSystemTheme } from "./lib/appearance.js";

applyAppearance(loadAppearance());
watchSystemTheme(loadAppearance);

// 鼠标操作不显示焦点框，键盘操作恢复。
const root = document.documentElement;
root.setAttribute("data-pointer-focus", "");
addEventListener("pointerdown", () => root.setAttribute("data-pointer-focus", ""), true);
addEventListener("keydown", event => {
  if (!event.ctrlKey && !event.metaKey && !event.altKey && event.key !== "Shift") root.removeAttribute("data-pointer-focus");
}, true);

createApp(App).mount("#app");
