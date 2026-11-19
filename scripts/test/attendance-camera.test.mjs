import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../../backend/app/services/attendance/share.html', import.meta.url), 'utf8');
function harness(getUserMedia) {
  const nodes = {
    video: { srcObject: null, play: async () => {}, getBoundingClientRect: () => ({left:0,top:0,width:390,height:844}) },
    scanFrame: { getBoundingClientRect: () => ({left:55,top:276,right:335,bottom:556,width:280}) },
    scanState: {},
  };
  const context = vm.createContext({ navigator: { mediaDevices: { getUserMedia } }, nodes });
  vm.runInContext(`let stream=null, cameraGeneration=0, scannerOpen=true, scanFrame=null, lastDecodeAt=0, lastFullDecodeAt=0;
    const el=id=>nodes[id]; function scanNextFrame(){} function cancelAnimationFrame(){}
    ${source.slice(source.indexOf('    function setScanState('), source.indexOf('    function closeScanner('))}
    ${source.slice(source.indexOf('    function scanRegion('), source.indexOf('    function decodeRegion('))}
    ${source.slice(source.indexOf('    async function startCamera('), source.indexOf('    async function capture('))}
  `, context);
  return {context, nodes, run: code => vm.runInContext(code, context)};
}
function media(focusFails = false) {
  const track = { stopped: 0, applied: null, stop() { this.stopped++; }, getCapabilities: () => ({focusMode:['continuous']}), async applyConstraints(value) { this.applied=value; if (focusFails) throw new Error('unsupported'); } };
  return { track, stream: { getTracks: () => [track], getVideoTracks: () => [track] } };
}

test('扫码优先高清后摄，启用连续对焦；不支持对焦不会阻止扫码', async () => {
  for (const fails of [false, true]) {
    const m=media(fails); let options;
    const h=harness(async value => { options=value; return m.stream; });
    await h.run('startCamera()');
    assert.equal(options.video.width.ideal,1920);
    assert.equal(options.video.height.ideal,1080);
    assert.equal(options.video.facingMode.ideal,'environment');
    assert.equal(m.track.applied.advanced[0].focusMode,'continuous');
    assert.equal(h.nodes.video.srcObject,m.stream);
  }
});

test('约束不支持时降级，拒绝权限不重复申请', async () => {
  const m=media(); let attempts=0;
  const h=harness(async () => { if (++attempts<3) throw Object.assign(new Error(),{name:'OverconstrainedError'}); return m.stream; });
  await h.run('startCamera()');
  assert.equal(attempts,3);
  let denied=0;
  const blocked=harness(async () => { denied++; throw Object.assign(new Error(),{name:'NotAllowedError'}); });
  await blocked.run('startCamera()');
  assert.equal(denied,1);
  assert.match(blocked.nodes.scanState.textContent,/权限/);
});

test('关闭后迟到的视频流被释放，不覆盖重新打开的相机', async () => {
  let resolve; const old=media(), current=media(); let count=0;
  const h=harness(() => ++count===1 ? new Promise(r=>{resolve=r;}) : Promise.resolve(current.stream));
  const pending=h.run('startCamera()');
  h.run('stopCamera()');
  await h.run('startCamera()');
  resolve(old.stream); await pending;
  assert.equal(old.track.stopped,1);
  assert.equal(h.nodes.video.srcObject,current.stream);
  assert.equal(current.track.stopped,0);
});

test('扫码框按 cover 裁切映射原始像素，保留静区且不越界', () => {
  const h=harness(async()=>{});
  const region=h.run('scanRegion(1920,1080)');
  assert.ok(region.x>0 && region.y>0);
  assert.ok(region.x+region.width<=1920 && region.y+region.height<=1080);
  assert.ok(region.width>280);
  assert.ok(Math.abs(region.width-region.height)<1);
  assert.match(source,/decodeRegion\(scanRegion\(width, height\), 1280\)/);
  assert.match(source,/now - lastFullDecodeAt >= 800/);
  assert.match(source,/decodeRegion\(\{x:0,y:0,width,height\}, 1600\)/);
});
