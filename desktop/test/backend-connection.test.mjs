import { test } from "node:test";
import assert from "node:assert/strict";
import {
  BACKEND_CONNECT_TIMEOUT_MS,
  waitForBackendHealth,
} from "../src/app/lib/backend-connection.js";

function clock() {
  let value = 0;
  return {
    now: () => value,
    wait: async ms => { value += ms; },
    get elapsed() { return value; },
  };
}

test("backend startup probes stay silent and keep retrying until the service is ready", async () => {
  const time = clock();
  let attempts = 0;
  const result = await waitForBackendHealth({
    probe: async () => {
      attempts += 1;
      if (attempts < 4) throw new Error("服务尚未启动");
      return { ok: true, version: "0.5.1" };
    },
    timeoutMs: 3000,
    retryMs: 500,
    now: time.now,
    wait: time.wait,
  });

  assert.equal(result.health.ok, true);
  assert.equal(result.error, null);
  assert.equal(result.attempts, 4);
  assert.equal(time.elapsed, 1500);
});

test("backend startup only reports the last connection error after the full timeout", async () => {
  const time = clock();
  const lastError = new Error("无法连接本地服务（8877）");
  const result = await waitForBackendHealth({
    probe: async () => { throw lastError; },
    timeoutMs: 2000,
    retryMs: 500,
    now: time.now,
    wait: time.wait,
  });

  assert.equal(result.health, null);
  assert.equal(result.error, lastError);
  assert.equal(result.attempts, 5);
  assert.equal(time.elapsed, 2000);
});

test("an empty health response uses the explicit timeout message", async () => {
  const result = await waitForBackendHealth({
    probe: async () => null,
    timeoutMs: 0,
    now: () => 0,
  });

  assert.equal(result.health, null);
  assert.match(result.error.message, /启动超时/);
  assert.equal(result.attempts, 1);
});

test("the default startup allowance is one minute", () => {
  assert.equal(BACKEND_CONNECT_TIMEOUT_MS, 60_000);
});
