import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

// 安装验收使用的版本定位必须与当前关于页保持一致。
test('Linux 安装验收核验实际平台与当前关于页版本', () => {
  const smoke = fs.readFileSync(new URL('../smoke-linux.mjs', import.meta.url), 'utf8');
  const about = fs.readFileSync(new URL('../../desktop/src/features/settings/components/AboutSettings.vue', import.meta.url), 'utf8');
  assert.ok(about.includes('class="about-version"'));
  assert.ok(smoke.includes("page.locator('.about-version').innerText()"));
  assert.ok(smoke.includes('process.platform'));
  assert.ok(smoke.includes('app.getVersion()'));
  assert.ok(!smoke.includes('.about-platform'));
});
