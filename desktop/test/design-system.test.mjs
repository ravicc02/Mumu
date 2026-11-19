import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
const read = path => readFile(new URL(path, import.meta.url), 'utf8');

test('桌面设计规范使用浅色事务画布、黑色操作与海蓝选中表面', async () => {
  const css = await read('../src/styles/workbench.css');
  assert.match(css, /--bg: #fbfbf5/);
  assert.match(css, /--ink: #000000/);
  assert.match(css, /--accent-soft: #cceeff/);
  assert.match(css, /--accent-wash: #e7f6ff/);
  assert.match(css, /--green: #1b7550; --green-soft: #d4f9e0/);
  assert.match(css, /--radius-pill: 9999px/);
  assert.match(css, /font-feature-settings: "ss03"/);
});

test('规范层接入入口并保留操作状态、布局与减少动效', async () => {
  const [main, css] = await Promise.all([read('../src/app/main.js'), read('../src/styles/design-system.css')]);
  assert.match(main, /import "\.\.\/styles\/design-system.css"/);
  assert.match(css, /button\.button[\s\S]*border-radius: var\(--radius-pill\)/);
  assert.match(css, /\.button\.dark:hover:not\(:disabled\)/);
  assert.match(css, /\.button\.dark:disabled/);
  assert.match(css, /\.search-field:focus-within/);
  assert.match(css, /prefers-reduced-motion: reduce/);
  assert.match(css, /\.platform-record-actions \{ flex-wrap: wrap/);
});

test('统计页保留七列对齐并去除分区式标题与冗余说明', async () => {
  const [usage, dialog] = await Promise.all([
    read('../src/features/settings/components/ModelUsage.vue'),
    read('../src/features/settings/components/SettingsDialog.vue'),
  ]);
  assert.match(usage, /<colgroup><col class="usage-col-time">/);
  assert.match(usage, /<th>供应商<\/th><th>模型<\/th>/);
  assert.match(usage, /class="usage-detail-row"><td colspan="7"/);
  assert.doesNotMatch(usage, /仅统计 Mumu 本地调用台账/);
  assert.match(dialog, /application-settings-header\{min-height:76px;[^}]*border-bottom:0;background:var\(--bg\)/);
});

test('模块层级覆盖工作区与设置，查询控件归入一个面板', async () => {
  const [css, usage, platform] = await Promise.all([read('../src/styles/design-system.css'), read('../src/features/settings/components/ModelUsage.vue'), read('../src/features/platforms/components/PlatformWorkspace.vue')]);
  assert.match(css, /--module-gap: 24px; --module-inset: 20px/);
  for (const selector of ['.course-group', '.learning-courses', '.attendance-rail', '.town-toolbar', '.detail-section', '.settings-section-stack']) assert.ok(css.includes(`body ${selector}`) || css.includes(selector));
  assert.match(usage, /<section class="usage-query-panel" aria-label="用量查询">/);
  assert.match(platform, /<section class="platform-record-panel" aria-label="授权记录">/);
  assert.doesNotMatch(css, /#usage-content \{ margin-top: -/);
});

test('启动与次级弹窗继承统一规范而不改变业务状态', async () => {
  const css = await read('../src/styles/design-system.css');
  assert.match(css, /body \.modal-shell-close \{ border-radius: var\(--radius-pill\)/);
  assert.match(css, /body \.startup-experience \.startup-header nav button\.active/);
  assert.match(css, /body \.modal-actions \{ flex-wrap: wrap/);
  assert.match(css, /body \.authorization-stages li > span \{ border-radius: 50%/);
  assert.match(css, /body \.town-attention[^}]*backdrop-filter: none/);
});
