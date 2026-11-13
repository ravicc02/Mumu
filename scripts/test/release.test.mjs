import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { validVersion, checkVersion, synchronizeVersion } from '../version.mjs';
import { checkRelease, releaseNotes, writeReleaseManifest, verifyReleaseAssets } from '../release.mjs';

const hash = value => crypto.createHash('sha256').update(value).digest('hex');
function fixture(t, gitRepo = false) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'studio-release-tests-'));
  t.after(() => {
    if (!path.resolve(root).startsWith(path.resolve(os.tmpdir()) + path.sep) || !path.basename(root).startsWith('studio-release-tests-')) throw new Error('Invalid cleanup root');
    fs.rmSync(root, { recursive: true, force: true });
  });
  const put = (file, content) => { const target = path.join(root, file); fs.mkdirSync(path.dirname(target), { recursive: true }); fs.writeFileSync(target, content); };
  put('VERSION', '1.2.3\n'); put('package.json', JSON.stringify({ version: '1.2.3', description: 'retain me' }));
  put('desktop/package.json', JSON.stringify({ version: '1.2.3' }));
  put('desktop/package-lock.json', JSON.stringify({ version: '1.2.3', packages: { '': { version: '1.2.3' }, 'node_modules/vendor': { version: '9.8.7' } } }));
  for (const file of ['README.md', 'AGENTS.md', 'CONTRIBUTING.md', 'docs/README.md', 'docs/releasing.md', '.github/SECURITY.md', 'THIRD_PARTY_NOTICES.md',
    'third_party/ocs/upstream/LICENSE', 'third_party/open-mascot/LICENSE', 'desktop/src/components/leibox/LICENSE.md',
    'desktop/public/third-party-notices.txt', 'desktop/public/licenses/open-mascot.txt', 'backend/app/services/attendance/JSQR-LICENSE', 'docs/assets/logo.svg', 'docs/assets/banner.svg', 'docs/assets/workspace.png']) put(file, 'fixture\n');
  put('CHANGELOG.md', '# Changelog\n\n## [Unreleased]\n\n## [1.2.3] - 2026-11-13\n\n### Fixed\n\n- Confirmed change.\n');
  put('.gitignore', 'desktop/release/\n');
  const git = (...args) => execFileSync('git', ['-C', root, ...args], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim();
  if (gitRepo) {
    git('init', '-b', 'main'); git('config', 'user.name', 'Release fixture'); git('config', 'user.email', 'fixture@example.invalid');
    git('add', '.'); git('-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=' + path.join(root, 'empty-hooks'), 'commit', '-m', 'Fixture');
    git('update-ref', 'refs/remotes/origin/main', 'HEAD'); git('tag', 'v1.2.3');
  }
  return { root, put, git };
}

test('version syntax accepts stable and prerelease versions without ambiguous numeric identifiers', () => {
  for (const value of ['0.4.2', '1.0.0', '0.5.0-rc.1']) assert.equal(validVersion(value), true);
  for (const value of ['v1.0.0', '01.0.0', '1.0', '1.0.0-01', '1.0.0-rc..1', '1.0.0+build', '1.0.0\n']) assert.equal(validVersion(value), false, value);
});

test('version synchronization preserves unrelated metadata and vendor versions', t => {
  const { root } = fixture(t); synchronizeVersion('1.3.0-rc.1', root);
  assert.equal(checkVersion(root), '1.3.0-rc.1');
  const lock = JSON.parse(fs.readFileSync(path.join(root, 'desktop/package-lock.json'), 'utf8'));
  assert.equal(lock.packages['node_modules/vendor'].version, '9.8.7');
  assert.equal(JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8')).description, 'retain me');
});

test('version and lockfile drift are rejected before a release', t => {
  const { root, put } = fixture(t);
  put('desktop/package-lock.json', JSON.stringify({ version: '1.2.3', packages: { '': { version: '0.0.1' } } }));
  assert.throws(() => checkVersion(root), /root package/);
});

test('release notes extract only a complete dated version section', () => {
  const text = '## [Unreleased]\n### Added\n- Future\n## [1.2.3] - 2026-11-13\n### Fixed\n- Real fix\n## [1.2.2] - 2026-11-12\n### Fixed\n- Old\n';
  assert.equal(releaseNotes(text, '1.2.3'), '### Fixed\n- Real fix\n');
  assert.throws(() => releaseNotes(text, '1.2.4'), /no dated entry/);
  assert.throws(() => releaseNotes(text.replace('2026-11-13', '2026-02-30'), '1.2.3'), /invalid date/);
  assert.throws(() => releaseNotes(text.replace('Real fix', 'TODO'), '1.2.3'), /incomplete/);
});

test('source validation checks local documentation links without requiring a release', t => {
  const { root, put } = fixture(t);
  assert.equal(checkRelease(root).version, '1.2.3');
  put('README.md', '[Missing](docs/missing.md)');
  assert.throws(() => checkRelease(root), /missing.*local link/);
});

test('strict validation requires clean mainline source and the exact version tag', t => {
  const { root, put } = fixture(t, true);
  assert.equal(checkRelease(root, { strict: true, tag: 'v1.2.3' }).version, '1.2.3');
  assert.throws(() => checkRelease(root, { strict: true, tag: 'v1.2.4' }), /tag must equal/);
  put('README.md', 'Uncommitted change');
  assert.throws(() => checkRelease(root, { strict: true }), /clean checkout/);
});

test('release manifest verifies the actual bundled source identity and hashes all published files', t => {
  const { root, put, git } = fixture(t, true), directory = path.join(root, 'desktop/release');
  const app = 'desktop/release/win-unpacked/resources/backend/_internal/app/';
  put(app + 'VERSION', '1.2.3\n');
  const build = { app_version: '1.2.3', git_commit: git('rev-parse', 'HEAD'), working_tree_dirty: false, built_at: '2026-11-13T00:00:00Z', build_fingerprint: 'fixture', source_hashes: { VERSION: hash('1.2.3\n') } };
  put(app + 'diagnostics/diagnostic-build.json', JSON.stringify(build));
  put('desktop/release/Assignment-Studio-1.2.3-windows-x64-setup.exe', 'fixture executable');
  put('desktop/release/RELEASE_NOTES.md', 'fixture notes'); put('desktop/release/build-environment.txt', 'fixture dependency');
  put('desktop/release/smoke-result.json', JSON.stringify({ version: '1.2.3', desktop_product_version: '1.2.3', packaged_backend_healthy: true, isolated_data: true, installed_app_verified:true, first_run_verified:true, optional_setup_skips_verified:true, packaged_brand_assets_verified:true }));
  const manifest = writeReleaseManifest(root, directory);
  assert.equal(manifest.files.length, 4);
  assert.equal(verifyReleaseAssets(directory,'1.2.3').verified,true);
  const sums = fs.readFileSync(path.join(directory, 'SHA256SUMS.txt'), 'utf8').trim().split('\n');
  assert.equal(sums.length, 5);
  for (const line of sums) { const [digest, name] = line.split('  '); assert.equal(digest, hash(fs.readFileSync(path.join(directory, name)))); }
  build.source_hashes.VERSION = 'bad'; put(app + 'diagnostics/diagnostic-build.json', JSON.stringify(build));
  assert.throws(() => writeReleaseManifest(root, directory), /fingerprint differs/);
  put(app + 'VERSION', '0.0.1\n');
  assert.throws(() => writeReleaseManifest(root, directory), /identity does not match/);
  put('desktop/release/Assignment-Studio-1.2.3-windows-x64-setup.exe', 'corrupt installer');
  assert.throws(() => verifyReleaseAssets(directory,'1.2.3'), /checksum differs/);
});
