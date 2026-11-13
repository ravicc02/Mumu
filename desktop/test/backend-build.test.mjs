import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';

test('a failed PyInstaller process stops packaging instead of reusing an old backend', {skip:process.platform!=='win32',timeout:30000}, async t=>{
  const root=await fs.mkdtemp(path.join(os.tmpdir(),'studio-build-failure-'));
  t.after(async()=>{
    if(path.dirname(root)!==path.resolve(os.tmpdir()) || !path.basename(root).startsWith('studio-build-failure-')) throw new Error('Invalid build fixture root');
    await fs.rm(root,{recursive:true,force:true});
  });
  await fs.writeFile(path.join(root,'python.cmd'),'@echo off\r\nif "%~1"=="..\\scripts\\run_pyinstaller.py" exit /b 17\r\nexit /b 0\r\n');
  const script=fileURLToPath(new URL('../../scripts/build-backend.ps1',import.meta.url));
  const result=spawnSync('powershell.exe',['-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',script],{encoding:'utf8',env:{...process.env,PATH:root+path.delimiter+process.env.PATH},timeout:25000});
  assert.equal(result.error,undefined);
  assert.notEqual(result.status,0);
  assert.match(result.stderr,/PyInstaller backend build failed with exit code 17/);
  assert.ok(!result.stdout.includes('Backend bundle:'));
});
