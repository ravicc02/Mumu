export function createCollectionController({ fetchRuns, changed, interval = 1200, timers = globalThis }) {
  let states = {};
  let disposed = false;
  const generations = new Map();
  const polls = new Map();
  const requests = new Map();
  const running = accountId => Boolean(states[accountId]?.pending || states[accountId]?.run?.status === "running");
  function publish(accountId, patch) {
    if (disposed) return;
    states = { ...states, [accountId]: { ...states[accountId], ...patch } };
    changed(states);
  }
  function stopPolling(accountId) {
    if (polls.has(accountId)) timers.clearTimeout(polls.get(accountId));
    polls.delete(accountId);
  }
  function poll(accountId) {
    if (disposed || polls.has(accountId) || !running(accountId)) return;
    polls.set(accountId, timers.setTimeout(async () => {
      polls.delete(accountId);
      try { await refresh(accountId); }
      catch { /* The owning request reports errors; polling retries transient read failures. */ }
      poll(accountId);
    }, interval));
  }
  function refresh(accountId) {
    if (!accountId || disposed) return Promise.resolve();
    const generation = generations.get(accountId) || 0;
    const current = requests.get(accountId);
    if (current?.generation === generation) return current.promise;
    const request = { generation };
    request.promise = Promise.resolve().then(() => fetchRuns(accountId)).then(runs => {
      if (disposed || generation !== (generations.get(accountId) || 0)) return;
      publish(accountId, { runs, run: runs[0] || null });
      if (running(accountId)) poll(accountId);
      else stopPolling(accountId);
    }).finally(() => { if (requests.get(accountId) === request) requests.delete(accountId); });
    requests.set(accountId, request);
    return request.promise;
  }
  return {
    refresh,
    begin(accountId) {
      if (disposed || running(accountId)) return false;
      generations.set(accountId, (generations.get(accountId) || 0) + 1);
      publish(accountId, { pending: true, run: { status: "running", progress: 1, processed_courses: 0, total_courses: 0 } });
      poll(accountId);
      return true;
    },
    finish(accountId, run = null) {
      if (disposed) return;
      generations.set(accountId, (generations.get(accountId) || 0) + 1);
      publish(accountId, { pending: false, ...(run ? { run } : {}) });
      if (running(accountId)) poll(accountId);
      else stopPolling(accountId);
    },
    dispose() {
      disposed = true;
      for (const accountId of polls.keys()) stopPolling(accountId);
      requests.clear();
    }
  };
}
