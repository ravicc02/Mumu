import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { projectRoot, checkVersion, validVersion } from './version.mjs';
import { checkRelease, readProduct, releaseNotes } from './release.mjs';

const sha256 = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const linuxNames = version => ({
  appimage: `Mumu-${version}-linux-x64.AppImage`,
  deb: `Mumu-${version}-linux-amd64.deb`,
  notes: 'RELEASE_NOTES.md',
  environment: 'build-environment-linux-x64.txt',
  smoke: 'smoke-linux-result.json',
  manifest: 'release-manifest-linux-x64.json',
  checksums: 'SHA256SUMS-linux-x64.txt',
});

function assertVersion(version) {
  if (!validVersion(version)) throw new Error('Invalid release version');
  return version;
}

function readBuildInfo(root, directory, identity) {
  const unpacked = path.join(directory, 'linux-unpacked/resources/backend');
  const matches = [];
  function visit(current) {
    if (!fs.existsSync(current)) return;
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const full = path.join(current, entry.name);
      if (entry.isDirectory()) visit(full);
      else if (entry.name === 'diagnostic-build.json') matches.push(full);
    }
  }
  visit(unpacked);
  if (matches.length !== 1) throw new Error('Linux package must contain one diagnostic build identity');
  const build = JSON.parse(fs.readFileSync(matches[0], 'utf8'));
  const versionFile = path.join(path.dirname(path.dirname(matches[0])), 'VERSION');
  if (fs.readFileSync(versionFile, 'utf8').trim() !== identity.version || build.app_version !== identity.version || build.git_commit !== identity.git_commit || build.working_tree_dirty) {
    throw new Error('Packaged Linux backend identity does not match the clean release commit');
  }
  if (!build.source_hashes || !Object.keys(build.source_hashes).length) throw new Error('Linux backend has no source fingerprints');
  for (const [file, digest] of Object.entries(build.source_hashes)) {
    const source = path.resolve(root, file);
    if (!source.startsWith(root + path.sep) || sha256(fs.readFileSync(source)) !== digest) throw new Error('Linux packaged source fingerprint differs: ' + file);
  }
  return build;
}

function verifySmoke(directory, version, names) {
  const smoke = JSON.parse(fs.readFileSync(path.join(directory, names.smoke), 'utf8'));
  const checks = ['packaged_backend_healthy', 'appimage_started', 'deb_started', 'first_run_verified', 'persistence_verified', 'browser_profile_verified', 'secret_service_verified', 'auto_launch_verified', 'close_preserves_backend', 'workspace_views_verified', 'x11_verified', 'wayland_verified', 'isolated_data'];
  if (smoke.version !== version || checks.some(key => smoke[key] !== true)) throw new Error('Linux installation and runtime smoke evidence is incomplete');
}

export function writeLinuxReleaseManifest(root = projectRoot, directory = path.join(root, 'desktop/release')) {
  const info = checkRelease(root, { strict: true });
  const names = linuxNames(info.version);
  const build = readBuildInfo(root, directory, info);
  verifySmoke(directory, info.version, names);
  const required = [names.appimage, names.deb, names.notes, names.environment, names.smoke];
  for (const name of required) {
    if (!fs.existsSync(path.join(directory, name))) throw new Error('Missing Linux release file: ' + name);
  }
  const files = required.map(name => {
    const bytes = fs.readFileSync(path.join(directory, name));
    return { name, bytes: bytes.length, sha256: sha256(bytes) };
  });
  const manifest = {
    schema_version: 1,
    version: info.version,
    tag: 'v' + info.version,
    git_commit: info.git_commit,
    built_at: build.built_at,
    packaged_at: new Date().toISOString(),
    platform: 'linux',
    arch: 'x64',
    build_fingerprint: build.build_fingerprint,
    files,
  };
  const encoded = JSON.stringify(manifest, null, 2) + '\n';
  fs.writeFileSync(path.join(directory, names.manifest), encoded);
  fs.writeFileSync(path.join(directory, names.checksums), [
    ...files.map(file => file.sha256 + '  ' + file.name),
    sha256(Buffer.from(encoded)) + '  ' + names.manifest,
  ].join('\n') + '\n');
  return manifest;
}

export function verifyLinuxReleaseAssets(directory, version) {
  assertVersion(version);
  const names = linuxNames(version);
  const required = [names.appimage, names.deb, names.notes, names.environment, names.smoke, names.manifest];
  const checksumFile = path.join(directory, names.checksums);
  const lines = fs.readFileSync(checksumFile, 'utf8').trim().split(/\r?\n/);
  const entries = new Map();
  for (const line of lines) {
    const match = /^([a-f0-9]{64})  ([^/\\]+)$/.exec(line);
    if (!match || !required.includes(match[2]) || entries.has(match[2])) throw new Error('Invalid Linux release checksum entry');
    entries.set(match[2], match[1]);
  }
  if (entries.size !== required.length) throw new Error('Linux release checksum list is incomplete');
  for (const name of required) {
    if (!fs.existsSync(path.join(directory, name)) || sha256(fs.readFileSync(path.join(directory, name))) !== entries.get(name)) {
      throw new Error('Linux release checksum differs: ' + name);
    }
  }
  const manifest = JSON.parse(fs.readFileSync(path.join(directory, names.manifest), 'utf8'));
  if (manifest.version !== version || manifest.tag !== 'v' + version || manifest.platform !== 'linux' || manifest.arch !== 'x64') {
    throw new Error('Linux release manifest identity differs');
  }
  if (!Array.isArray(manifest.files) || manifest.files.length !== 5) throw new Error('Linux release manifest file list differs');
  const seen = new Set();
  for (const file of manifest.files) {
    if (!required.slice(0, 5).includes(file.name) || seen.has(file.name) || file.sha256 !== entries.get(file.name) || file.bytes !== fs.statSync(path.join(directory, file.name)).size) {
      throw new Error('Linux release manifest file differs');
    }
    seen.add(file.name);
  }
  verifySmoke(directory, version, names);
  return { version, tag: manifest.tag, git_commit: manifest.git_commit, assets: 7, verified: true };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const [command = 'manifest', ...args] = process.argv.slice(2);
    const version = args.includes('--version') ? args[args.indexOf('--version') + 1] : checkVersion();
    const directory = path.resolve(args.includes('--dir') ? args[args.indexOf('--dir') + 1] : 'desktop/release');
    if (command === 'notes') {
      const output = path.join(directory, 'RELEASE_NOTES.md');
      fs.mkdirSync(directory, { recursive: true });
      fs.writeFileSync(output, releaseNotes(fs.readFileSync(path.join(projectRoot, 'CHANGELOG.md'), 'utf8'), version));
      console.log(output);
    } else if (command === 'manifest') {
      console.log(JSON.stringify(writeLinuxReleaseManifest(projectRoot, directory), null, 2));
    } else if (command === 'verify') {
      console.log(JSON.stringify(verifyLinuxReleaseAssets(directory, version), null, 2));
    } else throw new Error('Usage: release-linux.mjs notes | manifest | verify [--version X.Y.Z] [--dir directory]');
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
