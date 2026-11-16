import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { api } from '../src/app/api.js';

test('assignment automation APIs stay local and preserve the group contract', async () => {
  const original = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url, ...options });
    return { ok: true, json: async () => ({}) };
  };
  try {
    const payload = {
      name: '计算机科学',
      account_ids: ['account-a', 'account-b'],
      course_filters: ['计算机科学'],
      windows: [{ days: [1, 2, 3, 4, 5], start: '08:00', end: '18:00' }],
      auto_submit: true,
      enabled: true,
    };
    await api.assignmentAutoGroups();
    await api.assignmentAutoGroupCourses(payload.account_ids);
    await api.createAssignmentAutoGroup(payload);
    await api.updateAssignmentAutoGroup('group-one', { enabled: false });
    await api.scanAssignmentAutoGroup('group-one');
    await api.deleteAssignmentAutoGroup('group-one');
    assert.ok(calls[0].url.endsWith('/assignment-auto-groups'));
    assert.ok(calls[1].url.endsWith('/assignment-auto-groups/courses'));
    assert.deepEqual(JSON.parse(calls[1].body), { account_ids: ['account-a', 'account-b'] });
    assert.deepEqual(JSON.parse(calls[2].body), payload);
    assert.ok(calls[3].url.endsWith('/assignment-auto-groups/group-one'));
    assert.equal(calls[3].method, 'PATCH');
    assert.ok(calls[4].url.endsWith('/assignment-auto-groups/group-one/scan'));
    assert.equal(calls[4].method, 'POST');
    assert.equal(calls[5].method, 'DELETE');
    for (const call of calls) {
      assert.ok(call.url.startsWith('http://127.0.0.1:8877/api/'));
      assert.ok(!/chaoxing\.com|cookie|password|storage_state/.test(call.body || ''));
    }
  } finally {
    globalThis.fetch = original;
  }
});

test('assignment page exposes list and grouped automation views', async () => {
  const app = await readFile(new URL('../src/app/App.vue', import.meta.url), 'utf8');
  const component = await readFile(new URL('../src/features/assignments/components/AssignmentAutomation.vue', import.meta.url), 'utf8');
  assert.match(app, /自动处理/);
  assert.match(app, /作业列表/);
  assert.match(app, /AssignmentAutomation/);
  assert.match(app, /label: "可处理"/);
  assert.match(app, /label: "不可处理"/);
  assert.doesNotMatch(app, /class="assignment-readiness"/);
  assert.doesNotMatch(app, /自动检测与执行/);
  assert.match(component, /检测时间段/);
  assert.match(component, /处理的课程/);
  assert.match(component, /选择账号/);
  assert.match(component, /自动提交/);
});
