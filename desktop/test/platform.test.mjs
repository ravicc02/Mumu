import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const platform = require('../electron/platform/index.cjs');
const product = require('../product.json');

test('Linux uses extensionless backend and distro browser candidates', () => {
  assert.equal(platform.executableName(product, 'linux'), 'mumu-backend');
  assert.deepEqual(platform.browserCandidates({ MUMU_CHROME_PATH: '/custom/chrome' }, 'linux').slice(0, 3), [
    '/custom/chrome', 'google-chrome', 'google-chrome-stable',
  ]);
});

test('Windows keeps legacy backend and browser candidates', () => {
  assert.equal(platform.executableName(product, 'win32'), 'mumu-backend.exe');
  assert.ok(platform.browserCandidates({}, 'win32').some(item => item.endsWith('chrome.exe')));
});
