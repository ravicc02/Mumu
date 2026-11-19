import fs from 'node:fs';
import os from 'node:os';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { projectRoot, checkVersion } from './version.mjs';
import { checkRelease, readProduct, verifyReleaseAssets } from './release.mjs';
import { verifyLinuxReleaseAssets } from './release-linux.mjs';

export function publicAssetNames(version, artifactId = 'Mumu') {
  return [`${artifactId}-${version}-windows-x64-setup.exe`, `Mumu-${version}-linux-x64.AppImage`, `Mumu-${version}-linux-amd64.deb`];
}

export function assertEditableRelease(release) {
  if (release && release.isDraft !== true) throw new Error('Published releases are immutable; create a new version instead.');
}

function gh(args, {json=false, missing=false}={}) {
  const result=spawnSync('gh',args,{encoding:'utf8',cwd:projectRoot});
  if(result.error) throw result.error;
  if(result.status!==0) {
    if(missing && /HTTP 404|release not found/i.test(result.stderr)) return null;
    throw new Error('GitHub command failed: '+result.stderr.trim());
  }
  return json ? JSON.parse(result.stdout) : result.stdout.trim();
}

export function manageRelease(action, tag, directory=path.join(projectRoot,'desktop/release')) {
  const version=checkVersion();
  const product=readProduct();
  checkRelease(projectRoot,{strict:true,tag});
  if(tag!=='v'+version) throw new Error('Tag differs from VERSION');
  const repository=process.env.GH_REPO || JSON.parse(gh(['repo','view','--json','nameWithOwner'])).nameWithOwner;
  if(!/^[\w.-]+\/[\w.-]+$/.test(repository)) throw new Error('Invalid GitHub repository');
  const release=gh(['release','view',tag,'--repo',repository,'--json','isDraft,assets,tagName'],{json:true,missing:true});
  assertEditableRelease(release);
  if(action==='guard') return {tag,editable:true};
  const required = publicAssetNames(version, product.artifactId);
  const names = required.slice(0, 1);
  const linuxNames = required.slice(1);
  if(action==='draft') {
    verifyReleaseAssets(directory,version);
    const assets=names.map(name=>path.join(directory,name));
    const common=['--repo',repository,'--title',product.name+' '+tag,'--notes-file',path.join(directory,'RELEASE_NOTES.md')];
    if(release) {
      gh(['release','upload',tag,...assets,'--repo',repository,'--clobber']);
      gh(['release','edit',tag,'--draft',...common]);
    } else {
      gh(['release','create',tag,...assets,'--verify-tag','--draft',...common,...(version.includes('-')?['--prerelease']:[])]);
    }
    return {tag,draft:true,assets:names.length};
  }
  if(action==='publish') {
    if(!release) throw new Error('Build the release draft first');
    // 验收证据仅保留在构建记录；公开下载只含安装包，源码由 GitHub 标签归档提供。
    if(release.assets.length!==required.length || !required.every(name=>release.assets.some(asset=>asset.name===name))) throw new Error('Windows and Linux draft release assets are incomplete');
    const download=fs.mkdtempSync(path.join(os.tmpdir(),'mumu-release-verify-'));
    try {
      gh(['release','download',tag,'--repo',repository,'--dir',download]);
      const evidence=verifyReleaseAssets(directory,version);
      const linuxEvidence = verifyLinuxReleaseAssets(directory,version);
      for (const name of required) {
        const digest = file => createHash('sha256').update(fs.readFileSync(file)).digest('hex');
        if (digest(path.join(download, name)) !== digest(path.join(directory, name))) throw new Error('Downloaded installer differs from verified build: ' + name);
      }
      const commit=checkRelease(projectRoot,{strict:true,tag}).git_commit;
      if(evidence.git_commit!==commit) throw new Error('Downloaded release was built from another commit');
      if(linuxEvidence && linuxEvidence.git_commit !== commit) throw new Error('Downloaded Linux release was built from another commit');
      gh(['release','edit',tag,'--repo',repository,'--draft=false',version.includes('-')?'--prerelease':'--prerelease=false',version.includes('-')?'--latest=false':'--latest']);
      return {...evidence,linux:linuxEvidence,published:true};
    } finally {
      if(path.dirname(download)!==path.resolve(os.tmpdir()) || !path.basename(download).startsWith('mumu-release-verify-')) throw new Error('Invalid release verification directory');
      fs.rmSync(download,{recursive:true,force:true});
    }
  }
  if(action==='linux-draft') {
    if(!release || release.isDraft !== true) throw new Error('Build the Windows release draft first');
    verifyLinuxReleaseAssets(directory,version);
    const assets=linuxNames.map(name=>path.join(directory,name));
    gh(['release','upload',tag,...assets,'--repo',repository,'--clobber']);
    return {tag,draft:true,assets:linuxNames.length};
  }
  throw new Error('Unknown release action');
}

if(process.argv[1] && path.resolve(process.argv[1])===fileURLToPath(import.meta.url)) {
  try { const [action,tag,directory]=process.argv.slice(2); console.log(JSON.stringify(manageRelease(action,tag,directory),null,2)); }
  catch(error) { console.error(error.message); process.exitCode=1; }
}
