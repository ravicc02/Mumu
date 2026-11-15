import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source = await readFile(
  new URL('../src/features/settings/components/AttendanceDomainSettings.vue', import.meta.url),
  'utf8',
);

test('attendance relay settings default to official mode without exposing its address or secret', () => {
  assert.match(source, /const mode = ref\('official'\)/);
  assert.match(source, /官方中继服务/);
  assert.match(source, /自定义中继服务/);
  assert.match(source, /v-if="customMode"/);
  assert.match(source, /mode: 'official', base_url: '', admin_secret: ''/);
  assert.match(source, /!dirty \|\| \(customMode && !baseUrl\.trim\(\)\)/);
  assert.match(source, /savedCustomMode && status\.admin_secret_configured/);
  assert.doesNotMatch(source, /xxt\.raviportfolio\.cc/);
});
