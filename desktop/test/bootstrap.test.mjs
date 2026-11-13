import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
const { createBootstrap, safeOutput, verifyBackendHealth } = createRequire(import.meta.url)('../electron/bootstrap.cjs');
function fixture(options = {}) {
  const files = new Set(['/backend/mumu-backend.exe', '/chrome.exe']);
  const calls = [], ready = [];
  const io = { stat: async value => { if (!files.has(value.replaceAll('\\', '/'))) throw new Error('missing'); return { isFile: () => true }; }, mkdir: async () => {}, access: async () => {}, writeFile: async () => {}, unlink: async () => {} };
  const run = async (...args) => { calls.push(args); return { stdout: '', stderr: '' }; };
  const bootstrap = createBootstrap({ packaged: true, backendDirectory: '/backend', dataRoot: '/data', env: { MUMU_CHROME_PATH: '/chrome.exe' }, io, run, onReady: async (...args) => ready.push(args), ...options });
  return { bootstrap, files, calls, ready, io, run };
}
test('packaged app uses bundled libraries and never asks users to install Python', async () => {
  const { bootstrap, calls, ready } = fixture();
  const state = await bootstrap.check();
  assert.equal(state.status, 'ready'); assert.equal(state.backendReady, true);
  assert.equal(calls.length, 0); assert.equal(ready.length, 1);
  assert.equal(state.items.find(item => item.id === 'runtime').installable, false);
});
test('missing Python installs the runtime before pip and starts with the discovered interpreter', async () => {
  const base = fixture(), started = [], env = {MUMU_CHROME_PATH:'/chrome.exe',LOCALAPPDATA:'/fixture'};
  const { bootstrap } = fixture({packaged:false,python:'missing-python',env,io:base.io,onReady:async python=>started.push(python),run:async(file,args)=>{
    base.calls.push([file,args]);
    if(file==='missing-python') throw Object.assign(new Error('not found'),{code:'ENOENT'});
    if(file==='winget' && args.includes('Python.Python.3.13')) base.files.add('/fixture/Programs/Python/Python313/python.exe');
    return {stdout:'',stderr:''};
  }});
  assert.equal((await bootstrap.check()).items[0].missingPython,true);
  assert.equal((await bootstrap.install()).status,'ready');
  assert.equal(base.calls[1][0],'winget');
  assert.ok(base.calls[2][0].replaceAll('\\','/').endsWith('Python313/python.exe'));
  assert.deepEqual(base.calls[2][1].slice(0,3),['-m','pip','install']);
  assert.equal(started.length,1);
});
test('health must come from the expected backend version, not any service on the port', () => {
  assert.equal(verifyBackendHealth({ok:true,version:'0.5.0'},'0.5.0'),true);
  assert.throws(() => verifyBackendHealth({ok:true,version:'0.4.2'},'0.5.0'), {code:'VERSION_MISMATCH'});
  assert.throws(() => verifyBackendHealth({},'0.5.0'), /健康检查/);
});
test('missing browser gates startup and install uses fixed argv then verifies again', async () => {
  const base = fixture(); base.files.delete('/chrome.exe');
  const { bootstrap } = fixture({ io: base.io, run: async (...args) => { base.calls.push(args); base.files.add('/chrome.exe'); return { stdout: 'installed', stderr: '' }; } });
  assert.equal((await bootstrap.check()).status, 'blocked');
  assert.equal(base.calls.length, 0);
  assert.equal((await bootstrap.install()).status, 'ready');
  assert.equal(base.calls[0][0], 'winget'); assert.deepEqual(base.calls[0][1], ['install', '--id', 'Google.Chrome', '--exact', '--accept-package-agreements', '--accept-source-agreements']);
  assert.equal(base.calls[0][2].shell, undefined);
});
test('failed installation preserves retry and the manual command, without false readiness', async () => {
  const base = fixture(); base.files.delete('/chrome.exe');
  const { bootstrap } = fixture({ io: base.io, run: async () => { throw new Error('winget unavailable'); } });
  await bootstrap.check(); const state = await bootstrap.install();
  assert.equal(state.status, 'error'); assert.equal(state.backendReady, false);
  assert.match(state.output, /winget/); assert.match(state.error, /手动命令/); assert.match(state.items[1].command, /Google.Chrome/);
});
test('incomplete package does not try pip or start a missing backend', async () => {
  const base = fixture(); base.files.delete('/backend/mumu-backend.exe');
  const { bootstrap, calls, ready } = fixture({ io: base.io });
  const state = await bootstrap.check(); assert.equal(state.status, 'blocked');
  assert.equal(calls.length, 0); assert.equal(ready.length, 0); assert.match(state.items[0].command, /releases/);
});
test('source dependency installation uses the configured interpreter and exact requirements', async () => {
  const base = fixture(); let installed = false;
  const { bootstrap } = fixture({ packaged: false, io: base.io, python: 'custom python.exe', run: async (file, args, options) => {
    base.calls.push([file, args, options]);
    if (args[0] === '-c' && !installed) throw new Error('No module named browser_use');
    if (args[0] === '-m') installed = true;
    return { stdout: '', stderr: '' };
  } });
  assert.equal((await bootstrap.check()).status, 'blocked');
  assert.equal((await bootstrap.install()).status, 'ready');
  const install = base.calls.find(call => call[1][0] === '-m'); assert.equal(install[0], 'custom python.exe');
  assert.deepEqual(install[1].slice(0, 4), ['-m', 'pip', 'install', '-r']); assert.match(install[1][4], /requirements.txt$/);
});
test('unwritable storage and failed backend health cannot claim ready', async () => {
  const base = fixture();
  let fixtureA = fixture({ io: { ...base.io, access: async () => { throw new Error('denied'); } } });
  assert.equal((await fixtureA.bootstrap.check()).status, 'blocked'); assert.equal(fixtureA.ready.length, 0);
  fixtureA = fixture({ onReady: async () => { throw new Error('backend import failed'); } });
  const state = await fixtureA.bootstrap.check(); assert.equal(state.status, 'error'); assert.equal(state.backendReady, false);
});
test('dependency commands coalesce concurrent requests and redact secrets in output', async () => {
  let release; const waiting = new Promise(resolve => { release = resolve; });
  const { bootstrap } = fixture({ onReady: () => waiting });
  const a = bootstrap.check(), b = bootstrap.check(); assert.equal(a, b); release(); await a;
  assert.ok(!safeOutput('https://user:secret@example.test sk-123456789abc').includes('secret'));
  assert.ok(!safeOutput('sk-123456789abc').includes('123456789'));
});
