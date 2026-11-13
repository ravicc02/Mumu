import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { completeOnboarding, onboardingComplete, onboardingPreviouslyCompleted, environmentReady } from '../src/app/lib/onboarding.js';
const storage = () => { const data = new Map(); return { data, getItem: key => data.get(key), setItem: (key, value) => data.set(key, value) }; };
test('initialization is scoped to a data directory and records optional skips without credentials', () => {
  const store = storage(); assert.equal(onboardingComplete('/new', store), false);
  completeOnboarding('/new', { model: 'skipped', platform: 'skipped', api_key: 'never-save' }, store);
  assert.equal(onboardingComplete('/new', store), true); assert.equal(onboardingComplete('/other', store), false);
  assert.equal(onboardingPreviouslyCompleted(store), true);
  assert.ok(!JSON.stringify([...store.data.values()]).includes('never-save'));
});
test('legacy scoped completion is discovered before the current data root is known', () => {
  const key = 'studio:onboarding:v1:/legacy';
  const store = {
    getItem: name => name === key ? JSON.stringify({ version: 1, completed: true }) : null,
    length: 1,
    key: index => index === 0 ? key : null
  };
  assert.equal(onboardingPreviouslyCompleted(store), true);
});
test('failed storage and unknown environments cannot masquerade as completed setup', () => {
  assert.throws(() => completeOnboarding('', {}, storage()), /数据目录/);
  assert.throws(() => completeOnboarding('/new', {}, { setItem() { throw new Error(); } }), /无法保存/);
  for (const state of [null, { status: 'ready', backendReady: true, items: [] }, { status: 'blocked' }, { status: 'ready', backendReady: false, items: [{status:'ready'}] }]) assert.equal(environmentReady(state), false);
  assert.equal(environmentReady({ status: 'ready', backendReady: true, items: [{status:'ready'}] }), true);
});
test('startup does not reopen setup because a transient dependency check is not ready', async () => {
  const source = await readFile(new URL('../src/app/App.vue', import.meta.url), 'utf8');
  assert.match(source, /setupOpen = ref\(!onboardingPreviouslyCompleted\(\)\)/);
  assert.match(source, /await api\.onboarding\(\)/);
  assert.match(source, /void api\.completeOnboarding\(choices\)\.catch/);
  assert.doesNotMatch(source, /setupOpen\.value = \(await window\.desktopApi\.bootstrapState\(\)\)\.status/);
});
