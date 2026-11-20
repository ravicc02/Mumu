import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import net from 'node:net';
import {createRequire} from 'node:module';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';

if (process.platform !== 'linux') throw new Error('Linux smoke requires a Linux desktop session');
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const version = (await fs.readFile(path.join(root, 'VERSION'), 'utf8')).trim();
const release = path.join(root, 'desktop/release');
const temporary = await fs.mkdtemp(path.join(os.tmpdir(), 'mumu-linux-smoke-'));
const python = process.env.MUMU_PYTHON || 'python3';
const driver = execFileSync(python, ['-c', "import pathlib,playwright; print(pathlib.Path(playwright.__file__).parent/'driver'/'package')"], {encoding:'utf8'}).trim();
const {_electron} = createRequire(import.meta.url)(driver);
const browserPath = execFileSync(python, ['-c', "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); print(p.chromium.executable_path); p.stop()"], {encoding:'utf8'}).trim();
const evidence = {version, platform:'linux', arch:'x64', isolated_data:true, sessions:[]};

async function requireFreePort() {
  const server = net.createServer();
  await new Promise((resolve, reject) => server.once('error', reject).listen(8877, '127.0.0.1', resolve));
  await new Promise(resolve => server.close(resolve));
}

async function exercise(executable, name, platform) {
  const scope = path.join(temporary, name);
  const dataRoot = path.join(scope, 'data');
  const profile = path.join(scope, 'profile');
  const config = path.join(scope, 'config');
  const errors = [];
  const environment = {...process.env, MUMU_DATA:dataRoot, MUMU_CHROME_PATH:browserPath, XDG_CONFIG_HOME:config};
  if (name.startsWith('appimage')) environment.APPIMAGE_EXTRACT_AND_RUN = '1';
  let application;
  let accountId;
  for (let restart = 0; restart < 2; restart++) {
    await requireFreePort();
    try {
      application = await _electron.launch({executablePath:executable,
        args:['--user-data-dir='+profile, '--ozone-platform='+platform],
        env:environment, cwd:scope, timeout:60000});
      const page = await application.firstWindow();
      page.setDefaultTimeout(60000);
      page.on('pageerror', error => errors.push(error.message));
      if (restart === 0) {
        await page.getByRole('heading', {name:'准备浏览器环境', exact:true}).waitFor();
        await page.waitForFunction(async () => (await window.desktopApi.bootstrapState()).status === 'ready');
        const health = await (await fetch('http://127.0.0.1:8877/api/health')).json();
        assert.equal(health.ok, true);
        assert.equal(health.version, version);
        assert.equal(await fs.realpath(health.data_root), await fs.realpath(dataRoot));
        await page.getByRole('button', {name:'下一步', exact:true}).click();
        await page.getByRole('heading', {name:'连接模型服务', exact:true}).waitFor();
        await page.getByRole('button', {name:'稍后配置', exact:true}).click();
        await page.getByRole('heading', {name:'授权学习平台', exact:true}).waitFor();
        await page.waitForFunction(() => [...document.querySelectorAll('.startup-platform img')].every(item => item.complete && item.naturalWidth > 0));
        await page.getByRole('button', {name:'跳过，进入工作区', exact:true}).click();
        await page.locator('.startup-experience').waitFor({state:'hidden'});
        await page.evaluate(() => localStorage.setItem('mumu-linux-smoke', 'persisted'));
        const created = await fetch('http://127.0.0.1:8877/api/accounts', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name:'Linux fixture', platform:'chaoxing'})});
        assert.equal(created.ok, true, await created.text().then(text => text.slice(0, 200)));
        // Read the fixture through the normal account API on both launches.
        const accounts = await (await fetch('http://127.0.0.1:8877/api/accounts')).json();
        accountId = accounts[0].id;
        const initial = await page.evaluate(() => window.desktopApi.getAutoLaunch());
        assert.deepEqual(initial, {supported:true, enabled:false});
        assert.equal((await page.evaluate(() => window.desktopApi.setAutoLaunch(true))).enabled, true);
        assert.equal((await page.evaluate(() => window.desktopApi.setAutoLaunch(false))).enabled, false);
        const navigation = page.getByRole('navigation', {name:'主导航'});
        for (const label of ['平台', '作业', '网课', '签到', '面板']) {
          const button = navigation.getByRole('button', {name:label, exact:true});
          await button.click();
          await button.evaluate(element => {
            if (element.getAttribute('aria-current') !== 'page') throw new Error('Workspace navigation did not select the requested page');
          });
          assert.equal(await page.locator('.topbar-breadcrumb strong').innerText(), label);
          assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
        }
        await page.getByRole('button', {name:'帮助与关于', exact:true}).click();
        assert.equal(await application.evaluate(() => process.platform), 'linux');
        assert.equal(await application.evaluate(({app}) => app.getVersion()), version);
        assert.equal(await page.locator('.about-version').innerText(), `版本 ${version}`);
        await page.getByRole('button', {name:'关闭设置', exact:true}).click();
        await page.screenshot({path:path.join(scope, 'workspace.png')});
        const windowId = await application.evaluate(({BrowserWindow}) => BrowserWindow.getAllWindows()[0].id);
        await application.evaluate(({BrowserWindow}, id) => BrowserWindow.fromId(id).close(), windowId);
        assert.equal((await (await fetch('http://127.0.0.1:8877/api/health')).json()).ok, true);
        assert.equal(await application.evaluate(({BrowserWindow}, id) => !BrowserWindow.fromId(id).isDestroyed(), windowId), true);
      } else {
        await page.locator('.dashboard-heading').waitFor();
        assert.equal(await page.locator('.startup-experience').count(), 0);
        assert.equal(await page.evaluate(() => localStorage.getItem('mumu-linux-smoke')), 'persisted');
        const accounts = await (await fetch('http://127.0.0.1:8877/api/accounts')).json();
        assert.equal(accounts.some(account => account.id === accountId), true);
      }
      assert.equal(await page.locator('.brand-mark img').evaluate(image => image.complete && image.naturalWidth > 0), true);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    } finally {
      if (application) await application.close();
      application = null;
    }
    // Quit must stop the backend owned by this application.
    for (let attempt = 0; attempt < 40; attempt++) {
      try { await requireFreePort(); break; }
      catch (error) {
        if (attempt === 39) throw error;
        await new Promise(resolve => setTimeout(resolve, 250));
      }
    }
  }
  assert.deepEqual(errors, []);
  evidence.sessions.push({package:name, display:platform, backend:true, first_run:true, restart:true, auto_launch:true, close_preserves_backend:true, console_errors:errors});
}

await fs.mkdir(path.join(temporary, 'appimage-x11'), {recursive:true});
await fs.mkdir(path.join(temporary, 'deb-x11'), {recursive:true});
await fs.mkdir(path.join(temporary, 'appimage-wayland'), {recursive:true});
await exercise('/opt/Mumu/mumu', 'deb-x11', 'x11');
const appimage = path.join(release, `Mumu-${version}-linux-x64.AppImage`);
await exercise(appimage, 'appimage-x11', 'x11');
await exercise(appimage, 'appimage-wayland', 'wayland');

execFileSync(python, ['-c', `
import asyncio, os
from pathlib import Path
from playwright.async_api import async_playwright
from app.config import Settings
from app.services.agents.secrets import secret_store_for
settings = Settings(data_root=Path(os.environ['MUMU_DATA']))
settings.ensure_directories()
assert settings.data_root.stat().st_mode & 0o777 == 0o700
store = secret_store_for(settings)
store.put('linux-smoke', 'isolated-smoke-credential')
assert store.get('linux-smoke') == 'isolated-smoke-credential'
store.delete('linux-smoke')
async def check():
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(str(settings.account_profile_dir('fixture')), executable_path=os.environ['MUMU_CHROME_PATH'], headless=False)
        page = await context.new_page()
        await page.set_content('<title>Mumu isolated browser</title>')
        assert await page.title() == 'Mumu isolated browser'
        await context.close()
asyncio.run(check())
`], {cwd:root, env:{...process.env, PYTHONPATH:path.join(root,'backend'), MUMU_DATA:path.join(temporary,'runtime'), MUMU_CHROME_PATH:browserPath}, stdio:'inherit'});
Object.assign(evidence, {packaged_backend_healthy:true, appimage_started:true, deb_started:true, first_run_verified:true, persistence_verified:true, browser_profile_verified:true, secret_service_verified:true, auto_launch_verified:true, close_preserves_backend:true, workspace_views_verified:true, x11_verified:true, wayland_verified:true});
await fs.writeFile(path.join(release, 'smoke-linux-result.json'), JSON.stringify(evidence, null, 2)+'\n');
console.log('Linux AppImage, installed deb, X11, headless Weston Wayland, Secret Service, browser profile and persistence verified.');
