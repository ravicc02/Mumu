import { test } from 'node:test';
import assert from 'node:assert/strict';
import { attendanceRunning, attendanceRunnable, attendanceStates, attendanceKinds, attendanceWindowOpen, attendanceWindowLabel } from '../src/lib/attendance.js';
import { supportsBusiness, accountsForBusiness } from '../src/lib/platforms.js';
import { api } from '../src/api.js';
import { attendanceLocation } from '../src/lib/attendance.js';

test('location input never invents coordinates and requires a complete valid pair and address', () => {
  assert.equal(attendanceLocation('', '', ''), null);
  assert.deepEqual(attendanceLocation(30.5, 114.3, ' Classroom ', 'browser'), { latitude: 30.5, longitude: 114.3, address: 'Classroom', source: 'browser' });
  for (const values of [['', 114.3, 'Classroom'], [30.5, '', 'Classroom'], [91, 114.3, 'Classroom'], [30.5, Infinity, 'Classroom'], [30.5, 114.3, ' ']]) {
    assert.throws(() => attendanceLocation(...values));
  }
});

test('the list only offers verified activities inside the current start and end times', () => {
  const now = Date.parse('2026-11-13T04:00:00Z');
  const row = { start_time: now / 1000 - 10, end_time: now / 1000 + 10, availability_status: 'open', verified_at: 'verified' };
  assert.equal(attendanceWindowOpen(row, now), true);
  assert.equal(attendanceWindowOpen({ ...row, end_time: now / 1000 }, now), false);
  assert.equal(attendanceWindowOpen({ ...row, start_time: now / 1000 + 1 }, now), false);
  assert.equal(attendanceWindowOpen({ ...row, availability_status: 'unverified' }, now), false);
  assert.equal(attendanceWindowOpen({ ...row, verified_at: null }, now), false);
  assert.equal(attendanceWindowOpen({ start_time: '1758868045000' }, now), false);
});

test('missing deadlines are not interpreted as an indefinite valid sign-in', () => {
  const now = Date.now(), row = { start_time: now / 1000 - 10, end_time: null, availability_status: 'open', verified_at: 'verified' };
  assert.equal(attendanceWindowOpen(row, now), false);
  assert.equal(attendanceWindowOpen({ ...row, open_ended: 1 }, now), true);
});

test('the visible attendance window uses China time including the year', () => {
  const label = attendanceWindowLabel({ start_time: Date.parse('2026-11-13T16:05:00Z') / 1000, end_time: Date.parse('2026-11-13T17:05:00Z') / 1000 });
  assert.ok(label.includes('2026') && label.includes('11/14') && label.includes('00:05') && label.includes('01:05'), label);
});

test('attendance scopes include only explicitly supported platforms and preserve account identity', () => {
  const accounts = [{ id: 'cx1', platform: 'chaoxing' }, { id: 'edu', platform: 'educoder' }, { id: 'cx2', platform: 'chaoxing', capabilities: { attendance: false } }];
  assert.deepEqual(accountsForBusiness(accounts, 'attendance').map(account => account.id), ['cx1']);
  assert.equal(supportsBusiness('educoder', 'attendance'), false);
  assert.equal(supportsBusiness('yuketang', 'attendance'), false);
});

test('unknown submissions require reconciliation while missing input and cancelled work can continue', () => {
  const batch = states => ({ items: states.map(status => ({ status })) });
  assert.equal(attendanceRunning(batch(['queued', 'succeeded'])), true);
  assert.equal(attendanceRunning(batch(['submitting'])), true);
  assert.equal(attendanceRunnable(batch(['awaiting_input', 'succeeded'])), true);
  assert.equal(attendanceRunnable(batch(['cancelled'])), true);
  assert.equal(attendanceRunnable(batch(['unknown', 'pending'])), false);
  assert.equal(attendanceRunnable(batch(['succeeded', 'already_signed'])), false);
  assert.equal(attendanceRunnable(batch(['manual_required'])), false);
  assert.equal(attendanceRunnable(undefined), false);
  assert.equal(attendanceStates.unknown, '待核对');
  assert.equal(attendanceKinds.location, '位置');
});

test('batch and share actions use local scoped APIs without sending login credentials', async () => {
  const original = globalThis.fetch, calls = [];
  globalThis.fetch = async (url, options = {}) => { calls.push({ url, ...options }); return { ok: true, json: async () => ({}) }; };
  try {
    await api.collectAttendance('one/two');
    await api.createAttendanceBatch(['activity-a', 'activity-b']);
    await api.attendanceAction('batch-one', 'execute', { code: '1234', qr: '' });
    await api.shareAttendance('batch-one', { minutes: 15, base_url: '' });
    await api.revokeAttendanceShare('share-one');
    assert.ok(calls[0].url.endsWith('/accounts/one%2Ftwo/collect'));
    assert.deepEqual(JSON.parse(calls[1].body), { activity_ids: ['activity-a', 'activity-b'] });
    assert.ok(calls[2].url.endsWith('/batches/batch-one/actions/execute'));
    assert.equal(calls[4].method, 'DELETE');
    for (const call of calls) {
      assert.ok(!call.url.includes('chaoxing.com'));
      assert.ok(!/cookie|password|storage_state|platform_user_id/.test(call.body || ''));
    }
  } finally { globalThis.fetch = original; }
});
