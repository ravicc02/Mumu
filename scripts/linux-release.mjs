import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { checkVersion } from './version.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const version = checkVersion(root);
const directory = path.join(root, 'desktop', 'release');
const artifacts = [
  `Mumu-${version}-linux-x64.AppImage`,
  `Mumu-${version}-linux-amd64.deb`,
  'RELEASE_NOTES.md',
  'build-environment.txt',
];
const hash = file => crypto.createHash('sha256').update(fs.readFileSync(path.join(directory, file))).digest('hex');
const appImage = path.join(directory, artifacts[0]);
const deb = path.join(directory, artifacts[1]);
if (!fs.existsSync(appImage)) {
  const candidate = fs.readdirSync(directory).find(file => file.endsWith('.AppImage') && file.startsWith(`Mumu-${version}-linux-`));
  if (candidate) fs.renameSync(path.join(directory, candidate), appImage);
}
if (!fs.existsSync(deb)) {
  const candidate = fs.readdirSync(directory).find(file => file.endsWith('.deb') && file.startsWith(`Mumu-${version}-linux-`));
  if (candidate) fs.renameSync(path.join(directory, candidate), deb);
}
for (const file of artifacts) if (!fs.existsSync(path.join(directory, file))) throw new Error(`Linux release asset missing: ${file}`);
const files = artifacts.map(name => ({ name, bytes: fs.statSync(path.join(directory, name)).size, sha256: hash(name) }));
const manifest = {
  schema_version: 1,
  version,
  tag: `v${version}`,
  platform: 'linux',
  arch: 'x64',
  artifacts: ['AppImage', 'deb'],
  files,
};
const manifestName = 'release-manifest-linux-x64.json';
fs.writeFileSync(path.join(directory, manifestName), JSON.stringify(manifest, null, 2) + '\n');
const checksums = [...files, { name: manifestName, sha256: hash(manifestName) }]
  .map(file => `${file.sha256}  ${file.name}`).join('\n') + '\n';
fs.writeFileSync(path.join(directory, 'SHA256SUMS-linux-x64.txt'), checksums);
console.log(JSON.stringify(manifest, null, 2));
