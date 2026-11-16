import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

test("主导航高亮位移与按钮高度和间距使用同一尺寸", async () => {
  const app = await readFile(new URL("../src/app/App.vue", import.meta.url), "utf8");
  const styles = await readFile(new URL("../src/styles/workbench.css", import.meta.url), "utf8");

  assert.match(app, /const navItemHeight = 64;\s*const navItemGap = 4;/);
  assert.match(app, /navItems\.findIndex\(item => item\.id === activePage\) \* \(navItemHeight \+ navItemGap\)/);
  assert.match(app, /'--nav-item-height': `\$\{navItemHeight\}px`, '--nav-item-gap': `\$\{navItemGap\}px`/);
  assert.match(styles, /\.side-nav \{[^}]*gap: var\(--nav-item-gap\)/);
  assert.match(styles, /\.nav-item \{[^}]*height: var\(--nav-item-height\); flex: none/);
  assert.match(styles, /\.nav-motion-track \{[^}]*height: var\(--nav-item-height\)/);
});
