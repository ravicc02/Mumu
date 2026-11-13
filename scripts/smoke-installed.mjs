import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { execFile, execFileSync } from 'node:child_process';
import { promisify } from 'node:util';

if (process.platform !== 'win32' || process.env.GITHUB_ACTIONS !== 'true') throw new Error('Installer verification runs only on an isolated GitHub Windows runner.');
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const version = (await fs.readFile(path.join(root,'VERSION'),'utf8')).trim();
const manifest = JSON.parse(await fs.readFile(path.join(root,'desktop/package.json'),'utf8'));
const product = JSON.parse(await fs.readFile(path.join(root,'desktop/product.json'),'utf8'));
const release = path.join(root,'desktop/release');
const installer = path.join(release,product.artifactId+'-'+version+'-windows-x64-setup.exe');
const installRoot = await fs.mkdtemp(path.join(os.tmpdir(),'mumu-install-'));
const dataRoot = path.join(installRoot,'verification-data'), profileRoot = path.join(installRoot,'verification-profile');
const driver = execFileSync('python',['-c',"import pathlib,playwright; print(pathlib.Path(playwright.__file__).parent/'driver'/'package')"],{encoding:'utf8'}).trim();
const { _electron } = createRequire(import.meta.url)(driver);
const execute = promisify(execFile);
let app, backendPid;
try {
  const occupied = execFileSync('powershell.exe',['-NoProfile','-NonInteractive','-Command',"Get-NetTCPConnection -LocalPort 8877 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess; exit 0"],{encoding:'utf8'}).trim();
  if (occupied) throw new Error('Installer smoke port is occupied; no existing service was stopped.');
  await execute(installer,['/S','/D='+installRoot],{timeout:120000,windowsHide:true,maxBuffer:1024*1024});
  const executable = path.join(installRoot,manifest.build.productName+'.exe');
  await fs.access(executable);
  app = await _electron.launch({executablePath:executable,args:['--no-sandbox','--user-data-dir='+profileRoot],cwd:installRoot,
    env:{...process.env,MUMU_DATA:dataRoot},timeout:60000});
  const page = await app.firstWindow(); page.setDefaultTimeout(60000);
  await page.getByRole('heading',{name:'准备运行环境',exact:true}).waitFor();
  await page.waitForFunction(()=>!document.querySelector('.startup-footer .button.dark')?.disabled);
  const state = await page.evaluate(()=>window.desktopApi.bootstrapState());
  assert.equal(state.status,'ready'); assert.equal(state.backendReady,true);
  assert.equal(state.items.find(item=>item.id==='runtime').installable,false);
  const health = await (await fetch('http://127.0.0.1:8877/api/health')).json();
  assert.equal(health.version,version);
  assert.equal(await fs.realpath(health.data_root),await fs.realpath(dataRoot));
  backendPid = Number(execFileSync('powershell.exe',['-NoProfile','-NonInteractive','-Command',"Get-NetTCPConnection -LocalPort 8877 -State Listen | Select-Object -First 1 -ExpandProperty OwningProcess"],{encoding:'utf8'}).trim());
  const images = await page.locator('.startup-experience img').evaluateAll(items=>items.map(item=>({loaded:item.complete&&item.naturalWidth>0})));
  assert.ok(images.length && images.every(item=>item.loaded),'Packaged logo must load.');
  await fs.mkdir(path.join(root,'.runtime/installed-smoke'),{recursive:true});
  await page.screenshot({path:path.join(root,'.runtime/installed-smoke/environment.png')});
  await page.getByRole('button',{name:'下一步',exact:true}).click();
  await page.getByRole('heading',{name:'连接你的模型',exact:true}).waitFor();
  await page.getByRole('button',{name:'稍后配置',exact:true}).click();
  await page.getByRole('heading',{name:'加入学习平台',exact:true}).waitFor();
  assert.equal(await page.locator('.startup-platform').count(),7);
  await page.waitForFunction(()=>[...document.querySelectorAll('.startup-platform img')].every(item=>item.complete&&item.naturalWidth>0));
  const platformImages = await page.locator('.startup-platform img').evaluateAll(items=>items.every(item=>item.complete&&item.naturalWidth>0));
  assert.equal(platformImages,true,'Installed platform logos must load.');
  await page.getByRole('button',{name:'跳过，进入工作区',exact:true}).click();
  await page.locator('.startup-experience').waitFor({state:'hidden'});
  await page.reload(); await page.locator('.dashboard-heading').waitFor();
  assert.equal(await page.locator('.startup-experience').count(),0);
  assert.equal(await page.locator('.brand h1').innerText(),product.name);
  const evidencePath = path.join(release,'smoke-result.json');
  const evidence = JSON.parse((await fs.readFile(evidencePath,'utf8')).replace(/^\uFEFF/,''));
  Object.assign(evidence,{installed_app_verified:true,first_run_verified:true,optional_setup_skips_verified:true,packaged_brand_assets_verified:true});
  await fs.writeFile(evidencePath,JSON.stringify(evidence,null,2)+'\n');
  console.log('Installed '+version+': native dependencies, first-run flow, optional skips, local persistence and brand assets verified.');
} finally {
  if(app) await app.close().catch(()=>{});
  if(Number.isSafeInteger(backendPid) && backendPid>0) {
    // The port was empty before this app launched; only its recorded backend can be terminated.
    const remaining = execFileSync('powershell.exe',['-NoProfile','-NonInteractive','-Command',"Get-NetTCPConnection -LocalPort 8877 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty OwningProcess; exit 0"],{encoding:'utf8'}).trim();
    if(Number(remaining)===backendPid) await execute('taskkill',['/PID',String(backendPid),'/T','/F'],{windowsHide:true}).catch(()=>{});
  }
}
