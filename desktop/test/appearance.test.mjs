import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { APPEARANCE_KEY, applyAppearance, loadAppearance, resolveTheme, saveAppearance } from '../src/app/lib/appearance.js';

const memory = (value) => ({ getItem: () => value, setItem(key, next) { this.saved = [key, next]; } });
function fakeRoot(initial = []) {
  const classes = new Set(initial);
  return {
    classes,
    classList: { contains: name => classes.has(name), add: name => classes.add(name), remove: name => classes.delete(name), toggle: (name, force) => (force ? classes.add(name) : classes.delete(name)) },
    getBoundingClientRect() {},
  };
}

test('外观偏好缺失或损坏时回到跟随系统', () => {
  assert.deepEqual(loadAppearance(memory(null)), { theme: 'system', reduceMotion: false });
  assert.deepEqual(loadAppearance(memory('{坏')), { theme: 'system', reduceMotion: false });
  assert.deepEqual(loadAppearance(memory('{"theme":"neon","reduceMotion":"yes"}')), { theme: 'system', reduceMotion: false });
  assert.deepEqual(loadAppearance(memory('{"theme":"dark","reduceMotion":true}')), { theme: 'dark', reduceMotion: true });
});

test('保存外观偏好使用固定键', () => {
  const storage = memory(null);
  saveAppearance({ theme: 'light', reduceMotion: false }, storage);
  assert.equal(storage.saved[0], APPEARANCE_KEY);
  assert.deepEqual(JSON.parse(storage.saved[1]), { theme: 'light', reduceMotion: false });
});

test('主题解析：显式选择优先，跟随系统时看系统偏好', () => {
  assert.equal(resolveTheme('dark', false), 'dark');
  assert.equal(resolveTheme('light', true), 'light');
  assert.equal(resolveTheme('system', true), 'dark');
  assert.equal(resolveTheme('system', false), 'light');
});

test('应用外观只写 html 类，主题变化时暂停过渡一帧', () => {
  const frames = [];
  globalThis.requestAnimationFrame = callback => frames.push(callback);
  const root = fakeRoot();
  assert.equal(applyAppearance({ theme: 'dark', reduceMotion: true }, root, false), 'dark');
  assert.ok(root.classes.has('dark') && root.classes.has('reduce-motion') && root.classes.has('ds-theme-switching'));
  while (frames.length) frames.shift()();
  assert.ok(!root.classes.has('ds-theme-switching'));
  applyAppearance({ theme: 'dark', reduceMotion: false }, root, false);
  assert.ok(!root.classes.has('ds-theme-switching'), '主题未变化时不暂停过渡');
  assert.ok(!root.classes.has('reduce-motion'));
  delete globalThis.requestAnimationFrame;
});

test('首屏脚本在框架挂载前同步写入主题类', async () => {
  const html = await readFile(new URL('../index.html', import.meta.url), 'utf8');
  assert.ok(html.indexOf('studio:appearance') < html.indexOf('/src/app/main.js'));
  assert.match(html, /classList\.add\("dark"\)/);
  const main = await readFile(new URL('../src/app/main.js', import.meta.url), 'utf8');
  assert.ok(main.indexOf('tokens.css') < main.indexOf('workbench.css'));
  assert.match(main, /data-pointer-focus/);
});
