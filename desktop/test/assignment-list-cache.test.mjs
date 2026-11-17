import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createAssignmentListCache } from '../src/features/assignments/lib/list-cache.js';

const item = (accountId, id, assessment = {}) => ({ account_id: accountId, id, assessment });
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
};

test('account switches restore cached records while independent reads settle', async () => {
  const pending = { a: deferred(), b: deferred() };
  const changes = [];
  const cache = createAssignmentListCache({ fetchAssignments: id => pending[id].promise, changed: (id, records) => changes.push([id, records]) });
  const first = cache.refresh('a');
  assert.equal(cache.refresh('a'), first);
  const second = cache.refresh('b');
  pending.b.resolve([item('b', 'b-one')]); await second;
  assert.deepEqual(cache.read('a'), []);
  assert.equal(cache.read('b')[0].id, 'b-one');
  pending.a.resolve([item('a', 'a-one')]); await first;
  assert.equal(cache.read('a')[0].id, 'a-one');
  assert.equal(cache.read('b')[0].id, 'b-one');
  assert.equal(changes.length, 2);
});

test('failed reads preserve the previous collection, but a verified empty read replaces it', async () => {
  let response = [item('a', 'one')];
  const cache = createAssignmentListCache({ fetchAssignments: async () => { if (response instanceof Error) throw response; return response; }, changed() {} });
  await cache.refresh('a');
  response = new Error('offline');
  await assert.rejects(cache.refresh('a'), /offline/);
  assert.equal(cache.read('a')[0].id, 'one');
  response = [];
  await cache.refresh('a');
  assert.deepEqual(cache.read('a'), []);
});

test('reinspection and completed collection defeat late reads from older requests', async () => {
  const older = deferred(), newer = deferred(); let call = 0;
  const cache = createAssignmentListCache({ fetchAssignments: () => (++call === 1 ? older.promise : newer.promise), changed() {} });
  const request = cache.refresh('a'); await Promise.resolve();
  cache.patch('a', item('a', 'one', { automation_supported: true }));
  older.resolve([item('a', 'one', { automation_supported: false })]); await request;
  assert.equal(cache.read('a')[0].assessment.automation_supported, true);
  const refreshed = cache.refresh('a', { fresh: true });
  newer.resolve([item('a', 'two')]); await refreshed;
  assert.equal(cache.read('a')[0].id, 'two');
});

test('foreign responses cannot overwrite a matching account snapshot', async () => {
  const cache = createAssignmentListCache({ fetchAssignments: async () => [item('b', 'wrong')], changed() {} });
  cache.patch('a', item('a', 'saved'));
  await assert.rejects(cache.refresh('a'), /账号不一致/);
  assert.equal(cache.read('a')[0].id, 'saved');
});

test('data-root changes and account deletion prevent pending reads from restoring old records', async () => {
  for (const action of ['clear', 'retain', 'dispose']) {
    const pending = deferred(); const changes = [];
    const cache = createAssignmentListCache({ fetchAssignments: () => pending.promise, changed: (...args) => changes.push(args) });
    cache.patch('a', item('a', 'saved'));
    const request = cache.refresh('a'); await Promise.resolve();
    action === 'retain' ? cache.retain([]) : cache[action]();
    pending.resolve([item('a', 'late')]); await request;
    assert.deepEqual(cache.read('a'), []);
    assert.equal(changes.length, 1);
  }
});
