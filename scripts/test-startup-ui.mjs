import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(import.meta.url);
const modules = process.env.BRAND_NODE_MODULES;
const { chromium } = modules ? require(path.join(modules, 'playwright')) : await import('playwright');
const sharp = modules ? require(path.join(modules, 'sharp')) : require('sharp');
const origin = process.env.BRAND_PREVIEW_URL || 'http://127.0.0.1:5174';
const catalog = JSON.parse(await fs.readFile(path.join(root, 'backend/app/services/platforms/catalog.json'), 'utf8'));
const version = (await fs.readFile(path.join(root,'VERSION'),'utf8')).trim();
const preferences = await (await fetch('http://127.0.0.1:8877/api/preferences')).json();
const output = path.join(root, '.runtime/startup-ui'); await fs.mkdir(output, { recursive: true });
const browser = await chromium.launch({ executablePath: process.env.BRAND_CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true });
const ready = { status: 'ready', backendReady: true, scope: 'C:/Mumu-UI-Fixture', items: [
  {id:'runtime',label:'内置运行环境',status:'ready',detail:'由安装包提供'},
  {id:'browser',label:'Chrome / Edge',status:'ready',detail:'Chrome 已安装'},
  {id:'storage',label:'本地数据目录',status:'ready',detail:'C:/Mumu-UI-Fixture'}] };
async function session({ native = false, nativeReady = false, existing = false, reduced = false, width = 1440, height = 940 } = {}) {
  const context = await browser.newContext({ viewport: { width, height }, reducedMotion: reduced ? 'reduce' : 'no-preference' });
  if (native) await context.addInitScript(({state,nativeReady}) => {
    let current = nativeReady ? state : { ...state, status: 'blocked', backendReady: false, items: state.items.map(item => item.id === 'browser' ? {...item,status:'missing',installable:true,command:'winget install --id Google.Chrome --exact'} : item) };
    window.desktopApi = { bootstrapState: async () => current, checkDependencies: async () => current,
      installDependencies: async () => { current = state; return current; } };
  }, {state:ready,nativeReady});
  const page = await context.newPage(); page.setDefaultTimeout(8000);
  const errors = [], writes = []; let accounts = existing ? [{id:'fixture-account',name:'演示账号',platform:'chaoxing',status:'ready',platform_user_id:'demo-user',identity_verified_at:'2026-11-13T00:00:00Z'}] : [], configured = false;
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/**', async route => {
    const request = route.request(), p = new URL(request.url()).pathname;
    let value = [];
    if (p === '/api/health') value = {ok:true,version,data_root:ready.scope,ai_configured:configured};
    else if (p === '/api/settings') value = {data_root:ready.scope,logs_root:ready.scope+'/logs',ai_configured:configured};
    else if (p === '/api/setup/environment') value = ready;
    else if (p === '/api/platforms') value = catalog;
    else if (p === '/api/preferences') value = preferences;
    else if (p === '/api/model-config') {
      if (request.method() === 'PUT') { configured = true; writes.push('model-saved'); }
      value = {provider_name:'fixture',base_url:'https://model.example/v1',models:configured?['fixture-model']:[],reasoning_effort:'high',api_key_configured:configured};
    } else if (p === '/api/accounts' && request.method() === 'POST') {
      value = {id:'fixture-account',platform:'chaoxing',name:'授权中',status:'pending'}; writes.push('account-created');
    } else if (p.endsWith('/login') && request.method() === 'POST') value = {status:'waiting'};
    else if (p.endsWith('/login-status')) {
      accounts = [{id:'fixture-account',name:'演示账号',platform:'chaoxing',status:'ready',platform_user_id:'demo-user',identity_verified_at:'2026-11-13T00:00:00Z'}];
      value = {status:'success',account_id:'fixture-account'};
    } else if (p === '/api/accounts') { if (existing) await new Promise(resolve => setTimeout(resolve,250)); value = accounts; }
    await route.fulfill({ json: value });
  });
  await page.goto(origin);
  if (existing && nativeReady) await page.locator('.dashboard-heading').waitFor();
  else await page.getByRole('heading',{name:'准备运行环境',exact:true}).waitFor();
  return { page, context, errors, writes };
}
async function snapshot(page, name) {
  await page.evaluate(() => Promise.all(document.getAnimations().filter(animation => animation.effect?.getComputedTiming().iterations !== Infinity).map(animation => animation.finished.catch(() => {}))));
  await page.screenshot({ path: path.join(output, name + '.png') });
}
try {
  let test = await session(); let page = test.page;
  await page.locator('.brand-orbit canvas').waitFor();
  await page.waitForFunction(() => document.querySelector('.brand-orbit canvas')?.dataset.ready === 'true');
  const first = await page.locator('.brand-orbit').screenshot();
  await page.waitForTimeout(400);
  const second = await page.locator('.brand-orbit').screenshot();
  assert.notDeepEqual(first, second, 'brand orbit should move');
  const stats = await sharp(second).stats(); assert.ok(stats.channels.some(channel => channel.stdev > 10), 'canvas must contain real rendered pixels');
  await snapshot(page, '01-environment');
  await page.getByRole('button',{name:'下一步',exact:true}).click();
  await page.getByRole('heading',{name:'连接你的模型',exact:true}).waitFor();
  assert.equal(await page.getByRole('button',{name:'下一步',exact:true}).isDisabled(), true);
  await page.getByRole('button',{name:'稍后配置',exact:true}).click();
  assert.equal(await page.locator('.startup-platform').count(), catalog.length);
  await snapshot(page, '03-platforms');
  await page.getByRole('button',{name:'跳过，进入工作区',exact:true}).click();
  await page.locator('.startup-experience').waitFor({state:'hidden'});
  const skipped = await page.evaluate(() => JSON.parse(localStorage.getItem('studio:onboarding:v1:C:/Mumu-UI-Fixture')));
  assert.equal(skipped.model, 'skipped'); assert.equal(skipped.platform, 'skipped');
  assert.equal(test.writes.length, 0);
  await page.reload(); await page.locator('.dashboard-heading').waitFor();
  assert.equal(await page.locator('.startup-experience').count(), 0);
  await page.getByRole('button',{name:'帮助与关于',exact:true}).click();
  await page.getByRole('button',{name:'重新初始化',exact:true}).click();
  await page.getByRole('heading',{name:'准备运行环境',exact:true}).waitFor();
  await page.getByRole('button',{name:'退出初始化',exact:true}).click();
  await page.locator('.startup-experience').waitFor({state:'hidden'});
  assert.deepEqual(test.errors, []); await test.context.close();
  console.log('PASS: environment gate, live canvas pixels, 7 platforms, optional skips, persistence.');

  test = await session({width:1180,height:800,reduced:true}); page = test.page;
  await page.getByRole('button',{name:'下一步',exact:true}).click();
  await page.getByLabel('API Key',{exact:true}).fill('fixture-value');
  await page.getByLabel('模型名称',{exact:true}).fill('fixture-model');
  await page.getByRole('button',{name:'添加',exact:true}).click();
  await page.getByRole('button',{name:'保存配置',exact:true}).click();
  await page.getByText('模型配置已保存',{exact:true}).first().waitFor();
  await snapshot(page,'02-model-1180');
  await page.getByRole('button',{name:'下一步',exact:true}).click();
  await page.getByRole('button',{name:'授权 学习通',exact:true}).click();
  await page.locator('.startup-accounts').getByText('已授权',{exact:true}).waitFor();
  await snapshot(page,'04-authorized-1180');
  await page.getByRole('button',{name:'开始使用',exact:true}).click();
  await page.locator('.startup-experience').waitFor({state:'hidden'});
  assert.deepEqual(test.writes,['model-saved','account-created']);
  const saved = await page.evaluate(() => localStorage.getItem('studio:onboarding:v1:C:/Mumu-UI-Fixture'));
  assert.equal(JSON.parse(saved).model,'configured'); assert.equal(JSON.parse(saved).platform,'authorized');
  assert.ok(!saved.includes('fixture-value')); assert.deepEqual(test.errors,[]); await test.context.close();
  console.log('PASS: model save, authorization result, final entry, reduced motion, 1180px PC.');

  test = await session({native:true}); page = test.page;
  assert.equal(await page.getByRole('button',{name:'下一步',exact:true}).isDisabled(),true);
  assert.match(await page.locator('.dependency-command').innerText(), /winget/);
  await snapshot(page,'05-missing-dependency');
  await page.getByRole('button',{name:'安装缺少的依赖',exact:true}).click();
  await page.waitForFunction(() => !document.querySelector('.startup-footer .button.dark')?.disabled);
  assert.equal(await page.getByRole('button',{name:'下一步',exact:true}).isEnabled(),true);
  assert.deepEqual(test.errors,[]); await test.context.close();
  console.log('PASS: native missing dependency stays gated, install result enables continuation.');

  test = await session({native:true,nativeReady:true,existing:true}); page = test.page;
  await page.reload(); await page.locator('.dashboard-heading').waitFor();
  assert.equal(await page.locator('.startup-experience').count(),0);
  assert.deepEqual(test.writes,[]); assert.deepEqual(test.errors,[]); await test.context.close();
  console.log('PASS: existing native workspaces enter directly and do not authorize again.');

  test = await session({native:true,existing:true}); page = test.page;
  await page.getByRole('button',{name:'安装缺少的依赖',exact:true}).click();
  await page.locator('.startup-experience').waitFor({state:'hidden'});
  assert.deepEqual(test.writes,[]); assert.deepEqual(test.errors,[]); await test.context.close();
  console.log('PASS: dependency recovery waits for account loading before resolving legacy setup.');
} finally { await browser.close(); }
