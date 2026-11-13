import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {createRequire} from 'node:module';
import {execFileSync} from 'node:child_process';
import afterPack from '../scripts/after-pack.cjs';

test('Windows EXE display versions preserve the release candidate suffix', {skip:process.platform!=='win32',timeout:30000}, async t=>{
  const root=await fs.mkdtemp(path.join(os.tmpdir(),'studio-metadata-'));
  t.after(async()=>{
    if(path.dirname(root)!==path.resolve(os.tmpdir()) || !path.basename(root).startsWith('studio-metadata-')) throw new Error('Invalid metadata fixture root');
    await fs.rm(root,{recursive:true,force:true});
  });
  const executable=path.join(root,'Fixture.exe');
  const require=createRequire(import.meta.url);
  await fs.copyFile(require('electron'),executable);
  await afterPack({electronPlatformName:'win32',appOutDir:root,packager:{projectDir:root,appInfo:{productFilename:'Fixture',version:'1.2.3-rc.4'}}});
  const info=JSON.parse(execFileSync('powershell.exe',['-NoProfile','-NonInteractive','-Command','(Get-Item -LiteralPath $env:STUDIO_METADATA_EXE).VersionInfo | Select-Object FileVersion,ProductVersion | ConvertTo-Json -Compress'],{encoding:'utf8',env:{...process.env,STUDIO_METADATA_EXE:executable}}).trim());
  assert.equal(info.FileVersion,'1.2.3-rc.4');
  assert.equal(info.ProductVersion,'1.2.3-rc.4');
});
