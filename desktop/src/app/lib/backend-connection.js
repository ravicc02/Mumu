export const BACKEND_CONNECT_TIMEOUT_MS = 60_000;
export const BACKEND_CONNECT_RETRY_MS = 500;

export async function waitForBackendHealth({
  probe,
  timeoutMs = BACKEND_CONNECT_TIMEOUT_MS,
  retryMs = BACKEND_CONNECT_RETRY_MS,
  now = Date.now,
  wait = ms => new Promise(resolve => setTimeout(resolve, ms)),
} = {}) {
  if (typeof probe !== "function") throw new TypeError("probe must be a function");

  const deadline = now() + Math.max(0, timeoutMs);
  let attempts = 0;
  let lastError = null;

  while (true) {
    attempts += 1;
    try {
      const health = await probe();
      if (health) return { health, error: null, attempts };
    } catch (error) {
      lastError = error;
    }

    const remaining = deadline - now();
    if (remaining <= 0) {
      return {
        health: null,
        error: lastError || new Error("本地服务启动超时，请重新检测。"),
        attempts,
      };
    }
    await wait(Math.min(Math.max(1, retryMs), remaining));
  }
}
