import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  attendanceAutoEffectiveInterval,
  attendanceAutoPhase,
  attendanceBatchDisplayStatus,
  attendanceCourseFilterSelection,
  attendanceConfigurationDue,
  attendanceKinds,
  attendanceMaterialLabels,
  attendancePollDelay,
  attendanceRunnable,
  attendanceRunning,
  attendanceScanFeedback,
  attendanceStates,
  attendanceWindowLabel,
  attendanceWindowOpen,
} from '../src/features/attendance/lib/model.js';
import { supportsBusiness, accountsForBusiness } from '../src/features/platforms/lib/catalog.js';
import { api } from '../src/app/api.js';
import { attendanceLocation } from '../src/features/attendance/lib/model.js';

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

test('automatic attendance windows enter prewarm and burst phases in China time', () => {
  const group = {
    interval_seconds: 60,
    windows: [{ days: [1], start: '10:00', end: '10:10' }],
  };
  assert.equal(attendanceAutoPhase(group, new Date('2026-11-16T09:58:59+08:00')), 'outside');
  assert.equal(attendanceAutoPhase(group, new Date('2026-11-16T09:59:30+08:00')), 'prewarm');
  assert.equal(attendanceAutoPhase(group, new Date('2026-11-16T10:00:00+08:00')), 'burst');
  assert.equal(attendanceAutoPhase(group, new Date('2026-11-16T10:03:01+08:00')), 'normal');
  assert.equal(attendanceAutoPhase(group, new Date('2026-11-16T10:10:00+08:00')), 'outside');
  assert.equal(attendanceAutoEffectiveInterval(group, new Date('2026-11-16T10:00:10+08:00')), 5);
  assert.equal(attendanceAutoEffectiveInterval(group, new Date('2026-11-16T09:59:30+08:00')), 20);
  assert.equal(attendanceAutoEffectiveInterval({}, new Date('2026-11-16T10:10:00+08:00')), 30);
  const overnight = { windows: [{ days: [1], start: '23:00', end: '01:00' }] };
  assert.equal(attendanceAutoPhase(overnight, new Date('2026-11-16T23:01:00+08:00')), 'burst');
  assert.equal(attendanceAutoPhase(overnight, new Date('2026-11-17T00:30:00+08:00')), 'normal');
  assert.equal(attendanceAutoPhase({ windows: [] }, new Date('2026-11-16T23:30:00+08:00')), 'outside');
});

test('course filters survive partial account failures but drop stale courses after a complete fetch', () => {
  const courses = [{ name: '计算机科学' }, { name: '软件工程' }];
  assert.deepEqual(
    attendanceCourseFilterSelection(['计算机科学', '旧课程'], courses, false),
    ['计算机科学'],
  );
  assert.deepEqual(
    attendanceCourseFilterSelection(['计算机科学', '旧课程'], courses, true),
    ['计算机科学', '旧课程'],
  );
});

test('auto group status reflects the latest batch instead of waiting forever', () => {
  assert.equal(attendanceBatchDisplayStatus({
    status: 'awaiting_input',
    items: [{ status: 'awaiting_input' }],
  }), 'awaiting_input');
  assert.equal(attendanceBatchDisplayStatus({
    status: 'finished',
    items: [{ status: 'manual_required' }, { status: 'awaiting_input' }],
  }), 'awaiting_input');
  assert.equal(attendanceBatchDisplayStatus({
    status: 'finished',
    items: [{ status: 'succeeded' }, { status: 'already_signed' }],
  }), 'succeeded');
  assert.equal(attendanceBatchDisplayStatus({
    status: 'running',
    items: [{ status: 'running' }],
  }), 'running');
});

test('manual scan feedback reports pending and unknown work instead of a false success', () => {
  assert.deepEqual(attendanceScanFeedback({
    status: 'awaiting_input',
    items: [{ status: 'awaiting_input' }, { status: 'awaiting_input' }],
  }), {
    kind: 'error',
    message: '检测完成：2 个账号待补充素材或输入',
  });
  assert.deepEqual(attendanceScanFeedback({
    status: 'needs_review',
    items: [{ status: 'unknown' }],
  }), {
    kind: 'error',
    message: '检测完成：1 个账号需要先核对结果，未重复提交',
  });
  assert.deepEqual(attendanceScanFeedback({
    status: 'completed',
    items: [{ status: 'succeeded' }, { status: 'already_signed' }],
  }), {
    kind: 'success',
    message: '检测完成：2 个账号已签到',
  });
});

test('attendance polling separates active work from idle configuration refresh', () => {
  assert.equal(attendancePollDelay(true), 750);
  assert.equal(attendancePollDelay(false), 8000);
  assert.equal(attendanceConfigurationDue(0, 60000), true);
  assert.equal(attendanceConfigurationDue(1000, 60999), false);
  assert.equal(attendanceConfigurationDue(1000, 61000), true);
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
  assert.equal(attendanceKinds.photo, '拍照');
  assert.equal(attendanceStates.manual_required, '需人工处理');
  assert.equal(attendanceMaterialLabels.photo, '使用账号照片素材');
  assert.equal(attendanceMaterialLabels.location, '使用位置签到素材');
});

test('attendance groups and relay settings use local scoped APIs without sending login credentials', async () => {
  const original = globalThis.fetch, calls = [];
  globalThis.fetch = async (url, options = {}) => { calls.push({ url, ...options }); return { ok: true, json: async () => ({}) }; };
  try {
    await api.attendanceProfile('account-a');
    await api.attendanceLocation();
    await api.saveAttendanceLocation({ latitude: 30.5, longitude: 114.3, address: '教室' });
    await api.attendanceQrGroups();
    await api.createAttendanceQrGroup({ name: '计算机科学 1 组', account_ids: ['account-a'], pin: '', base_url: '' });
    await api.renameAttendanceQrGroup('group-one', '计算机科学 2 组');
    await api.revokeAttendanceQrGroup('group-one');
    await api.attendanceAutoGroups();
    await api.attendanceAutoGroupCourses(['account-a', 'account-b']);
    await api.createAttendanceAutoGroup({ name: '自动组', account_ids: ['account-a'], kinds: ['normal', 'code'], course_filters: ['计算机科学', '软件工程'], interval_seconds: 60, enabled: true, location: null });
    await api.updateAttendanceAutoGroup('auto-one', { enabled: false });
    await api.scanAttendanceAutoGroup('auto-one');
    await api.deleteAttendanceAutoGroup('auto-one');
    await api.attendanceRelay();
    await api.testAttendanceRelay({ mode: 'custom', base_url: 'https://sign.example.com', admin_secret: 'x'.repeat(32) });
    await api.saveAttendanceRelay({ mode: 'custom', base_url: 'https://sign.example.com', admin_secret: 'x'.repeat(32) });
    assert.ok(calls[0].url.endsWith('/accounts/account-a/profile'));
    assert.deepEqual(JSON.parse(calls[2].body), { latitude: 30.5, longitude: 114.3, address: '教室' });
    assert.deepEqual(JSON.parse(calls[4].body), { name: '计算机科学 1 组', account_ids: ['account-a'], pin: '', base_url: '' });
    assert.equal(calls[5].method, 'PATCH');
    assert.equal(calls[6].method, 'DELETE');
    assert.equal(calls[8].url, 'http://127.0.0.1:8877/api/attendance/auto-groups/courses');
    assert.deepEqual(JSON.parse(calls[8].body), { account_ids: ['account-a', 'account-b'] });
    assert.deepEqual(JSON.parse(calls[9].body).course_filters, ['计算机科学', '软件工程']);
    assert.equal(calls[11].method, 'POST');
    assert.ok(calls[11].url.endsWith('/auto-groups/auto-one/scan'));
    assert.ok(calls[13].url.endsWith('/relay'));
    assert.ok(calls[14].url.endsWith('/relay/test'));
    assert.equal(calls[15].method, 'PUT');
    assert.deepEqual(JSON.parse(calls[14].body), { mode: 'custom', base_url: 'https://sign.example.com', admin_secret: 'x'.repeat(32) });
    for (const call of calls) {
      assert.ok(!call.url.includes('chaoxing.com'));
      assert.ok(!/cookie|password|storage_state|platform_user_id/.test(call.body || ''));
    }
  } finally { globalThis.fetch = original; }
});
