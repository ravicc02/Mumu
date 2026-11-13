import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { readProduct } from '../release.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const json = file => JSON.parse(fs.readFileSync(path.join(root, file), 'utf8'));

test('product metadata is shared by package, installer, backend and update surfaces', () => {
  const product = readProduct(root);
  const rootPackage = json('package.json');
  const desktopPackage = json('desktop/package.json');

  assert.equal(product.name, 'Mumu');
  assert.equal(rootPackage.name, 'mumu');
  assert.equal(rootPackage.repository.url, product.repository + '.git');
  assert.equal(desktopPackage.name, 'mumu-desktop');
  assert.equal(desktopPackage.build.appId, product.appId);
  assert.equal(desktopPackage.build.productName, product.name);
  assert.equal(desktopPackage.build.extraResources[0].from, '../backend/dist/mumu-backend');
  assert.equal(desktopPackage.build.win.artifactName, 'Mumu-${version}-windows-${arch}-setup.${ext}');
  assert.ok(desktopPackage.build.files.includes('product.json'));
});

test('legacy installation and data identifiers remain explicit compatibility entries', () => {
  const product = readProduct(root);
  const desktopPackage = json('desktop/package.json');
  const backendProduct = fs.readFileSync(path.join(root, 'backend/app/product.py'), 'utf8');

  assert.equal(product.legacyAppId, 'local.ai.assignment.studio');
  assert.equal(desktopPackage.build.nsis.guid, 'eb16fffb-9f96-5004-94a4-fb5b2f48fec9');
  assert.equal(product.dataDirectory, 'AIAssistantStudio');
  assert.match(backendProduct, /DATABASE_FILENAME = "answer-studio\.sqlite3"/);
});

test('current user-facing README does not advertise the legacy repository', () => {
  const readme = fs.readFileSync(path.join(root, 'README.md'), 'utf8');
  assert.doesNotMatch(readme, /github\.com\/ravicc02\/ai-quiz-assistant/);
  assert.match(readme, /github\.com\/ravicc02\/Mumu/);
});

test('reused implementations live in business modules without a source tree or provenance documents', () => {
  const desktopPackage = json('desktop/package.json');
  const ignore = fs.readFileSync(path.join(root, '.gitignore'), 'utf8');

  assert.equal(fs.existsSync(path.join(root, 'third_party')), false);
  assert.equal(fs.existsSync(path.join(root, 'THIRD_PARTY_NOTICES.md')), false);
  assert.equal(fs.existsSync(path.join(root, 'desktop/public/third-party-notices.txt')), false);
  assert.equal(fs.existsSync(path.join(root, 'desktop/public/platforms/SOURCES.md')), false);
  assert.deepEqual(Object.keys(desktopPackage.dependencies).sort(), [
    '@lucide/vue',
    'cannon-es',
    'qrcode',
    'three',
    'vue',
  ]);
  assert.match(ignore, /^\/AGENTS\.md$/m);
  assert.match(ignore, /^\/docs\/$/m);
});
