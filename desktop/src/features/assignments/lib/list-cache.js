// Durable snapshots live in SQLite; this cache keeps account switches immediate.
export function createAssignmentListCache({ fetchAssignments, changed }) {
  const snapshots = new Map();
  const revisions = new Map();
  const requests = new Map();
  let disposed = false;

  function advance(accountId) {
    revisions.set(accountId, (revisions.get(accountId) || 0) + 1);
    requests.delete(accountId);
  }

  function read(accountId) {
    return snapshots.get(accountId)?.slice() || [];
  }

  function publish(accountId, records) {
    snapshots.set(accountId, records.slice());
    changed(accountId, read(accountId));
  }

  function refresh(accountId, { fresh = false } = {}) {
    if (!accountId || disposed) return Promise.resolve();
    if (fresh) advance(accountId);
    if (requests.has(accountId)) return requests.get(accountId).promise;
    const revision = revisions.get(accountId) || 0;
    const request = {};
    request.promise = Promise.resolve().then(() => fetchAssignments(accountId)).then(records => {
      if (disposed || revision !== (revisions.get(accountId) || 0)) return;
      if (!Array.isArray(records) || records.some(item => !item || item.account_id !== accountId)) {
        throw new Error('作业缓存与当前账号不一致，请重试');
      }
      publish(accountId, records);
    }).finally(() => {
      if (requests.get(accountId) === request) requests.delete(accountId);
    });
    requests.set(accountId, request);
    return request.promise;
  }

  return {
    read,
    refresh,
    patch(accountId, record) {
      if (disposed || record?.account_id !== accountId) return;
      advance(accountId);
      const records = read(accountId);
      const index = records.findIndex(item => item.id === record.id);
      if (index < 0) records.push(record);
      else records[index] = record;
      publish(accountId, records);
    },
    retain(accountIds) {
      const retained = new Set(accountIds);
      for (const accountId of new Set([...snapshots.keys(), ...requests.keys()])) {
        if (!retained.has(accountId)) {
          advance(accountId);
          snapshots.delete(accountId);
        }
      }
    },
    clear() {
      for (const accountId of new Set([...snapshots.keys(), ...requests.keys()])) advance(accountId);
      snapshots.clear();
    },
    dispose() {
      disposed = true;
      snapshots.clear();
      requests.clear();
    },
  };
}
