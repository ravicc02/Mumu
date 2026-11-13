import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { projectRoot, checkVersion, validVersion } from './version.mjs';

const documents = [
  'README.md',
  'README.en.md',
  'README.zh-TW.md',
  'README.ko.md',
  'README.ja.md',
  'README.es.md',
  'CONTRIBUTING.md',
  'CHANGELOG.md',
  '.github/SECURITY.md',
];
const legalFiles = ['LICENSES.txt', 'desktop/public/licenses/OPEN_SOURCE_LICENSES.txt'];
const readmeAssets = [
  'assets/brand/mumu.png',
  'assets/brand/banner.svg',
  'assets/brand/logo.svg',
  'assets/brand/mumu-bots.gif',
  'assets/brand/mumu-bots.png',
  'assets/demo/demo.gif',
  'assets/demo/demo.mp4',
  'assets/platforms/xuexitong.png',
  'assets/platforms/educoder.png',
  'assets/platforms/zhihuishu.ico',
  'assets/platforms/icourse.ico',
  'assets/platforms/icve.ico',
  'assets/platforms/zjy.ico',
  'assets/platforms/yuketang.ico',
  'assets/screenshots/workspace.png',
];
const sha256 = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
function git(root, ...args) { return execFileSync('git', ['-C', root, ...args], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim(); }
export function readProduct(root = projectRoot) {
  const product = JSON.parse(fs.readFileSync(path.join(root, 'desktop/product.json'), 'utf8'));
  if (product.name !== 'Mumu'
    || product.repository !== 'https://github.com/ravicc02/Mumu'
    || product.appId !== 'io.github.ravicc02.mumu'
    || product.artifactId !== 'Mumu'
    || product.backendExecutable !== 'mumu-backend.exe') throw new Error('desktop/product.json contains an invalid product identity');
  return product;
}
const artifactName = (product, version) => product.artifactId + '-' + version + '-windows-x64-setup.exe';

export function releaseNotes(text, version) {
  if (!validVersion(version)) throw new Error('Invalid release version');
  const lines = text.split(/\r?\n/), start = lines.findIndex(line => line.startsWith('## [' + version + '] - '));
  if (start < 0) throw new Error('CHANGELOG.md has no dated entry for ' + version);
  if (!/^## \[[^\]]+\] - [0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(lines[start])) throw new Error('Release heading must include YYYY-MM-DD');
  const date = lines[start].slice(-10), timestamp = Date.parse(date + 'T00:00:00Z');
  if (!Number.isFinite(timestamp) || new Date(timestamp).toISOString().slice(0, 10) !== date) throw new Error('Release heading has an invalid date');
  const next = lines.findIndex((line, index) => index > start && /^## /.test(line));
  const body = lines.slice(start + 1, next < 0 ? undefined : next).join('\n').trim();
  if (!body || !/^### /m.test(body) || /待补充|TODO|TBD/.test(body)) throw new Error('Release notes are incomplete');
  return body + '\n';
}

function documentLinks(root, file, content) {
  const links = [...content.matchAll(/!?\[[^\]]*\]\(([^)]+)\)/g)].map(match => match[1].split(/\s/)[0]);
  links.push(...[...content.matchAll(/(?:src|href)="([^"]+)"/g)].map(match => match[1]));
  for (const link of links) {
    if (!link || /^(https?:|mailto:|#)/.test(link)) continue;
    const target = path.resolve(path.dirname(path.join(root, file)), decodeURIComponent(link.split('#')[0]));
    if (!target.startsWith(root + path.sep) || !fs.existsSync(target)) throw new Error(file + ': missing or invalid local link ' + link);
  }
}

export function checkRelease(root = projectRoot, { strict = false, tag = '' } = {}) {
  const version = checkVersion(root);
  const product = readProduct(root);
  for (const file of [...documents, ...legalFiles, ...readmeAssets]) {
    const full = path.join(root, file);
    if (!fs.existsSync(full) || !fs.statSync(full).isFile()) throw new Error('Required file missing: ' + file);
    if (documents.includes(file)) documentLinks(root, file, fs.readFileSync(full, 'utf8'));
  }
  if (!fs.readFileSync(path.join(root, legalFiles[0])).equals(fs.readFileSync(path.join(root, legalFiles[1])))) throw new Error('Packaged license text differs from LICENSES.txt');
  const changelog = fs.readFileSync(path.join(root, 'CHANGELOG.md'), 'utf8');
  if (!/^## \[Unreleased\]$/m.test(changelog)) throw new Error('CHANGELOG.md requires an Unreleased section');
  let commit = '', dirty = null;
  try { commit = git(root, 'rev-parse', 'HEAD'); dirty = Boolean(git(root, 'status', '--porcelain', '--untracked-files=all')); }
  catch (error) { if (strict) throw new Error('A Git checkout is required for release validation', { cause: error }); }
  if (strict) {
    releaseNotes(changelog, version);
    if (dirty) throw new Error('Release requires a clean checkout, including all intended source and vendor files');
    git(root, 'merge-base', '--is-ancestor', 'HEAD', 'origin/main');
    const tracked = git(root, 'ls-files').split('\n');
    if (tracked.some(file => /(^|\/)\.env($|\.(?!example$))|(^|\/)(storage-state|storage_state)\.json$|\.(sqlite3?|log)$/.test(file))) throw new Error('Runtime secrets or local data are tracked; review the index');
  }
  if (tag) {
    if (tag !== 'v' + version) throw new Error('Release tag must equal v' + version);
    if (git(root, 'rev-parse', tag + '^{commit}') !== commit) throw new Error('Release tag does not point to this commit');
  }
  return { version, product: product.name, git_commit: commit, working_tree_dirty: dirty, strict };
}

export function writeReleaseManifest(root = projectRoot, directory = path.join(root, 'desktop/release')) {
  const info = checkRelease(root, { strict: true });
  const executable = artifactName(readProduct(root), info.version);
  const bundle = path.join(directory, 'win-unpacked/resources/backend/_internal/app');
  const build = JSON.parse(fs.readFileSync(path.join(bundle, 'diagnostics/diagnostic-build.json'), 'utf8'));
  const packagedVersion = fs.readFileSync(path.join(bundle, 'VERSION'), 'utf8').trim();
  if (packagedVersion !== info.version || build.app_version !== info.version || build.git_commit !== info.git_commit || build.working_tree_dirty) throw new Error('Packaged backend identity does not match the clean release commit');
  if (!build.source_hashes || !Object.keys(build.source_hashes).length) throw new Error('Packaged backend has no source fingerprints');
  for (const [file, hash] of Object.entries(build.source_hashes)) {
    const source = path.resolve(root, file);
    if (!source.startsWith(root + path.sep) || sha256(fs.readFileSync(source)) !== hash) throw new Error('Packaged source fingerprint differs: ' + file);
  }
  const smoke = JSON.parse(fs.readFileSync(path.join(directory, 'smoke-result.json'), 'utf8').replace(/^\uFEFF/, ''));
  if (smoke.version !== info.version || smoke.packaged_backend_healthy !== true || smoke.desktop_product_version !== info.version || smoke.isolated_data !== true) throw new Error('Package smoke evidence is missing or does not match this version');
  if (smoke.installed_app_verified !== true || smoke.first_run_verified !== true || smoke.optional_setup_skips_verified !== true || smoke.packaged_brand_assets_verified !== true) throw new Error('Installed application and first-run verification evidence is missing');
  const assets = [executable, 'RELEASE_NOTES.md', 'build-environment.txt', 'smoke-result.json'];
  const files = assets.map(name => { const bytes = fs.readFileSync(path.join(directory, name)); return { name, bytes: bytes.length, sha256: sha256(bytes) }; });
  const manifest = { schema_version: 1, version: info.version, tag: 'v' + info.version, git_commit: info.git_commit,
    built_at: build.built_at, packaged_at: new Date().toISOString(), platform: 'windows', arch: 'x64',
    build_fingerprint: build.build_fingerprint, files };
  const encoded = JSON.stringify(manifest, null, 2) + '\n';
  fs.writeFileSync(path.join(directory, 'release-manifest.json'), encoded);
  fs.writeFileSync(path.join(directory, 'SHA256SUMS.txt'), [...files.map(file => file.sha256 + '  ' + file.name), sha256(Buffer.from(encoded)) + '  release-manifest.json'].join('\n') + '\n');
  return manifest;
}

export function verifyReleaseAssets(directory, version) {
  if (!validVersion(version)) throw new Error('Invalid release version');
  const product = readProduct();
  const installer = artifactName(product, version);
  const required = [installer, 'RELEASE_NOTES.md', 'build-environment.txt', 'smoke-result.json', 'release-manifest.json'];
  const lines = fs.readFileSync(path.join(directory, 'SHA256SUMS.txt'), 'utf8').trim().split(/\r?\n/);
  const entries = new Map();
  for (const line of lines) {
    const match = /^([a-f0-9]{64})  ([^/\\]+)$/.exec(line);
    if (!match || !required.includes(match[2]) || entries.has(match[2])) throw new Error('Invalid or duplicate release checksum entry');
    entries.set(match[2], match[1]);
  }
  if (entries.size !== required.length) throw new Error('Release checksum list is incomplete');
  for (const name of required) if (sha256(fs.readFileSync(path.join(directory,name))) !== entries.get(name)) throw new Error('Release checksum differs: ' + name);
  const manifest = JSON.parse(fs.readFileSync(path.join(directory,'release-manifest.json'),'utf8'));
  if (manifest.version !== version || manifest.tag !== 'v' + version || manifest.platform !== 'windows' || manifest.arch !== 'x64' || manifest.files?.length !== 4) throw new Error('Release manifest identity differs');
  const seen = new Set();
  for (const file of manifest.files) {
    if (!required.slice(0,4).includes(file.name) || seen.has(file.name) || file.sha256 !== entries.get(file.name) || file.bytes !== fs.statSync(path.join(directory,file.name)).size) throw new Error('Release manifest file differs');
    seen.add(file.name);
  }
  return {version, tag:manifest.tag, git_commit:manifest.git_commit, assets:6, verified:true};
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const [command = 'check', ...args] = process.argv.slice(2);
    const option = key => { const index = args.indexOf(key); return index < 0 ? '' : args[index + 1] || ''; };
    if (command === 'check') console.log(JSON.stringify(checkRelease(projectRoot, { strict: args.includes('--release'), tag: option('--tag') }), null, 2));
    else if (command === 'notes') {
      const version = option('--version') || checkVersion();
      const notes = releaseNotes(fs.readFileSync(path.join(projectRoot, 'CHANGELOG.md'), 'utf8'), version);
      const output = option('--out');
      if (output) { fs.mkdirSync(path.dirname(path.resolve(output)), { recursive: true }); fs.writeFileSync(output, notes); }
      else process.stdout.write(notes);
    } else if (command === 'manifest') console.log(JSON.stringify(writeReleaseManifest(projectRoot, option('--dir') ? path.resolve(option('--dir')) : undefined), null, 2));
    else if (command === 'verify') console.log(JSON.stringify(verifyReleaseAssets(path.resolve(option('--dir') || 'desktop/release'), option('--version') || checkVersion()), null, 2));
    else throw new Error('Usage: release.mjs check | notes | manifest | verify [--dir dir] [--version X.Y.Z]');
  } catch (error) { console.error(error.message); process.exitCode = 1; }
}
