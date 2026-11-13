import { test } from 'node:test';
import assert from 'node:assert/strict';
import { completeOnboarding, onboardingComplete, environmentReady } from '../src/lib/onboarding.js';
const storage = () => { const data = new Map(); return { data, getItem: key => data.get(key), setItem: (key, value) => data.set(key, value) }; };
test('initialization is scoped to a data directory and records optional skips without credentials', () => {
  const store = storage(); assert.equal(onboardingComplete('/new', store), false);
  completeOnboarding('/new', { model: 'skipped', platform: 'skipped', api_key: 'never-save' }, store);
  assert.equal(onboardingComplete('/new', store), true); assert.equal(onboardingComplete('/other', store), false);
  assert.ok(!JSON.stringify([...store.data.values()]).includes('never-save'));
});
test('failed storage and unknown environments cannot masquerade as completed setup', () => {
  assert.throws(() => completeOnboarding('', {}, storage()), /数据目录/);
  assert.throws(() => completeOnboarding('/new', {}, { setItem() { throw new Error(); } }), /无法保存/);
  for (const state of [null, { status: 'ready', backendReady: true, items: [] }, { status: 'blocked' }, { status: 'ready', backendReady: false, items: [{status:'ready'}] }]) assert.equal(environmentReady(state), false);
  assert.equal(environmentReady({ status: 'ready', backendReady: true, items: [{status:'ready'}] }), true);
});
