import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import { contrast, loadTokens, resolve } from './helpers/tokens.mjs';
const read = path => readFile(new URL(path, import.meta.url), 'utf8');
async function sources(dir, found = []) {
  for (const entry of await readdir(new URL(dir, import.meta.url), { withFileTypes: true })) {
    const path = dir + entry.name + (entry.isDirectory() ? '/' : '');
    if (entry.isDirectory()) await sources(path, found);
    else if (/\.(vue|css)$/.test(entry.name)) found.push(path);
  }
  return found;
}
const styleText = async path => {
  const text = await read(path);
  return path.endsWith('.vue') ? [...text.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map(match => match[1]).join('\n') : text;
};

test('token 分三层：旧变量只做别名，工作台样式不再定义颜色', async () => {
  const { css, light } = await loadTokens();
  const workbench = await read('../src/styles/workbench.css');
  for (const name of ['--ds-brand-primary', '--ds-text-primary', '--ds-surface-100', '--ds-surface-400', '--ds-radius-pill', '--ds-elevation-300', '--ds-motion-soft', '--ds-text-base']) assert.ok(light.has(name), name);
  assert.equal(light.get('--ds-text-base'), '13px');
  assert.equal(light.get('--ds-weight-control'), '500');
  assert.match(css, /--bg: var\(--ds-surface-200\)/);
  assert.match(css, /--radius-card: var\(--ds-radius-md\)/);
  assert.match(css, /--ds-radius-md: calc\(12px \* var\(--ds-corner-radius-scale, 1\)\)/);
  assert.doesNotMatch(workbench, /^:root \{/m);
  assert.doesNotMatch(css, /var\(--[a-z0-9-]+, var\(/);
});

test('暗色覆盖语义色、表面、阴影与交互态，且不覆盖形状与动效', async () => {
  const { darkOverrides, light } = await loadTokens();
  for (const name of ['--ds-brand-primary', '--ds-brand-primary-text', '--ds-text-primary', '--ds-text-secondary', '--ds-text-tertiary', '--ds-danger', '--ds-success', '--ds-warning', '--ds-surface-100', '--ds-surface-200', '--ds-surface-300', '--ds-surface-400', '--ds-on-surface', '--ds-on-surface-active', '--ds-overlay', '--ds-elevation-100', '--ds-elevation-200', '--ds-elevation-300', '--ds-input-shadow', '--ds-input-shadow-focus']) assert.ok(darkOverrides.has(name), name);
  for (const name of darkOverrides.keys()) assert.doesNotMatch(name, /radius|duration|ease|motion|space|--ds-text-(?:2xs|xs|sm|base|md|lg|xl)$/);
  assert.notEqual(darkOverrides.get('--ds-brand-primary'), light.get('--ds-brand-primary'));
});

test('亮暗两套主题的文字、品牌色与状态色满足 WCAG AA', async () => {
  const { light, dark } = await loadTokens();
  for (const [mode, map] of [['light', light], ['dark', dark]]) {
    const surfaces = ['--ds-surface-100', '--ds-surface-200', '--ds-surface-300', '--ds-surface-400', '--ds-selected-surface', '--ds-surface-muted', '--ds-surface-input'];
    for (const surface of surfaces) {
      const base = resolve(surface, map);
      for (const text of ['--ds-text-primary', '--ds-text-secondary', '--ds-text-tertiary']) assert.ok(contrast(resolve(text, map, base), base) >= 4.5, `${mode} ${text}/${surface}`);
    }
    for (const surface of ['--ds-surface-100', '--ds-surface-200']) {
      const base = resolve(surface, map);
      for (const name of ['--ds-brand-primary', '--ds-danger', '--ds-success', '--ds-warning']) assert.ok(contrast(resolve(name, map), base) >= 4.5, `${mode} ${name}/${surface}`);
    }
    for (const [fg, bg] of [['--ds-danger', '--ds-danger-bg'], ['--ds-success', '--ds-success-bg'], ['--ds-warning', '--ds-warning-bg'], ['--ds-selected-ink', '--ds-selected-surface']]) assert.ok(contrast(resolve(fg, map), resolve(bg, map)) >= 4.5, `${mode} ${fg}/${bg}`);
    const brand = resolve('--ds-brand-primary', map);
    assert.ok(contrast(resolve('--ds-brand-primary-text', map, brand), brand) >= 4.5, `${mode} 品牌色按钮文字`);
    const surface = resolve('--ds-surface-100', map);
    assert.ok(contrast(resolve('--ds-border-strong', map, surface), surface) >= 3, `${mode} 控件边框`);
  }
});

test('组件样式不写裸色值，层级使用 z-index token', async () => {
  const offenders = [];
  for (const path of await sources('../src/')) {
    if (/tokens\.css$|mascot\.css$|task-town\.css$/.test(path)) continue;
    const text = await styleText(path);
    for (const match of text.matchAll(/#[0-9a-fA-F]{3,8}\b/g)) offenders.push(`${path}: ${match[0]}`);
    for (const match of text.matchAll(/z-index:\s*(\d{2,})/g)) offenders.push(`${path}: z-index ${match[1]}`);
  }
  assert.deepEqual(offenders, []);
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
