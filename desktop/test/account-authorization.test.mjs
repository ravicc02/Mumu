import { test } from "node:test";
import assert from "node:assert/strict";
import { setImmediate } from "node:timers/promises";
import { createAccountAuthorization } from "../src/lib/account-authorization.js";

function fixture(overrides = {}) {
  const states = [], finished = [], timers = [], calls = [];
  const account = { id: "new-account", platform: "chaoxing", status: "ready", name: "平台姓名", platform_user_id: "123", identity_verified_at: "2026-11-12" };
  const api = {
    createAccount: async (...args) => { calls.push(["create", ...args]); return { id: "new-account", platform: "chaoxing" }; },
    login: async id => { calls.push(["login", id]); },
    loginStatus: async () => ({ status: "success", account_id: "new-account" }),
    accounts: async () => [account], ...overrides,
  };
  const flow = createAccountAuthorization(api, {
    changed: state => states.push(state),
    completed: (...args) => finished.push(args),
    schedule: callback => { const timer = { callback, active: true }; timers.push(timer); return timer; },
    unschedule: timer => { timer.active = false; },
  });
  return { flow, states, calls, account, finished, timers,
    async tick() { const timer = timers.find(item => item.active); assert.ok(timer); timer.active = false; timer.callback(); await setImmediate(); },
    get state() { return states.at(-1); },
  };
}

test("one authorization command opens login and reports success only after platform identity is saved", async () => {
  const f = fixture();
  await f.flow.start("chaoxing");
  assert.deepEqual(f.calls, [["create", "", "chaoxing"], ["login", "new-account"]]);
  assert.equal(f.state.status, "waiting");
  assert.equal(await f.flow.start("chaoxing"), false);
  await f.tick();
  assert.equal(f.state.status, "success");
  assert.equal(f.state.message, "添加成功");
  assert.equal(f.state.account.name, "平台姓名");
  assert.equal(f.finished.length, 1);
  f.flow.dispose();
});

test("duplicate authorization selects the existing account returned by the backend", async () => {
  const existing = { id: "existing", platform: "chaoxing", status: "ready", name: "已有账号", platform_user_id: "123", identity_verified_at: "today" };
  const f = fixture({ loginStatus: async () => ({ status: "success", account_id: "existing" }), accounts: async () => [existing] });
  await f.flow.start("chaoxing"); await f.tick();
  assert.equal(f.state.accountId, "existing");
  assert.equal(f.state.message, "授权已更新");
  assert.equal(f.finished[0][0].id, "existing");
  f.flow.dispose();
});

test("authorization errors stay retryable and reuse the same provisional account", async () => {
  let attempts = 0;
  const f = fixture({ login: async () => { if (++attempts === 1) throw new Error("登录窗口打开失败"); } });
  await f.flow.start("chaoxing");
  assert.equal(f.state.status, "error");
  assert.equal(f.state.error, "登录窗口打开失败");
  await f.flow.retry(); await f.tick();
  assert.equal(f.state.status, "success");
  assert.equal(f.calls.filter(item => item[0] === "create").length, 1);
  f.flow.dispose();
});

test("backend failure or expired session never becomes addition success", async () => {
  for (const status of ["error", "idle"]) {
    const f = fixture({ loginStatus: async () => ({ status, message: "授权已结束" }) });
    await f.flow.start("chaoxing"); await f.tick();
    assert.equal(f.state.status, "error");
    assert.equal(f.finished.length, 0);
    f.flow.dispose();
  }
});

test("an old backend without synced platform identity cannot claim success", async () => {
  const f = fixture({ accounts: async () => [{ id: "new-account", platform: "chaoxing", status: "ready", name: "待授权" }] });
  await f.flow.start("chaoxing"); await f.tick();
  assert.equal(f.state.status, "error");
  assert.match(f.state.error, /重启服务/);
  assert.equal(f.finished.length, 0);
  f.flow.dispose();
});

test("polling waits for each response and disposal ignores its late result", async () => {
  let resolve;
  const f = fixture({ loginStatus: () => new Promise(done => { resolve = done; }) });
  await f.flow.start("chaoxing"); await f.tick();
  assert.equal(f.timers.filter(timer => timer.active).length, 0);
  assert.equal(await f.flow.start("chaoxing"), false);
  f.flow.dispose();
  resolve({ status: "success", account_id: "new-account" }); await setImmediate();
  assert.equal(f.finished.length, 0);
  assert.equal(f.state.status, "waiting");
});

test("re-authorization updates an existing record without creating another account", async () => {
  const f = fixture();
  await f.flow.start("chaoxing", f.account); await f.tick();
  assert.equal(f.calls.filter(item => item[0] === "create").length, 0);
  assert.equal(f.state.message, "授权成功");
  assert.equal(f.state.mode, "update");
  f.flow.dispose();
});
