import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export function validVersion(value) {
  const match = /^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$/.exec(value);
  return Boolean(match && (!match[4] || match[4].split('.').every(part => !/^[0-9]+$/.test(part) || part === '0' || part[0] !== '0')));
}
export function readVersion(root = projectRoot) {
  const version = fs.readFileSync(path.join(root, 'VERSION'), 'utf8').trim();
  if (!validVersion(version)) throw new Error('VERSION must contain a SemVer version without v or build metadata');
  return version;
}
function manifests(root) {
  return ['package.json', 'desktop/package.json', 'desktop/package-lock.json', ...(fs.existsSync(path.join(root, 'package-lock.json')) ? ['package-lock.json'] : [])];
}
export function checkVersion(root = projectRoot) {
  const version = readVersion(root), errors = [];
  for (const file of manifests(root)) {
    const value = JSON.parse(fs.readFileSync(path.join(root, file), 'utf8'));
    if (value.version !== version) errors.push(file + ': expected ' + version + ', got ' + value.version);
    if (file.endsWith('package-lock.json') && value.packages?.['']?.version !== version) errors.push(file + ': root package version differs');
  }
  if (errors.length) throw new Error(errors.join('\n'));
  return version;
}
export function synchronizeVersion(version, root = projectRoot) {
  if (!validVersion(version)) throw new Error('Invalid version: ' + version);
  const pending = manifests(root).map(file => {
    const value = JSON.parse(fs.readFileSync(path.join(root, file), 'utf8'));
    value.version = version;
    if (file.endsWith('package-lock.json')) {
      if (!value.packages?.['']) throw new Error(file + ': lockfile root package is missing');
      value.packages[''].version = version;
    }
    return [file, JSON.stringify(value, null, 2) + '\n'];
  });
  for (const [file, content] of pending) fs.writeFileSync(path.join(root, file), content);
  fs.writeFileSync(path.join(root, 'VERSION'), version + '\n');
  return checkVersion(root);
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const [command = 'check', value] = process.argv.slice(2);
    if (command === 'check') console.log('Version aligned: ' + checkVersion());
    else if (command === 'sync') console.log('Version synchronized: ' + synchronizeVersion(readVersion()));
    else if (command === 'set' && value) console.log('Version synchronized: ' + synchronizeVersion(value));
    else throw new Error('Usage: node scripts/version.mjs check | sync | set X.Y.Z[-rc.N]');
  } catch (error) { console.error(error.message); process.exitCode = 1; }
}
