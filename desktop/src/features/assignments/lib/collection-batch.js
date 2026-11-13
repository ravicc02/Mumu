export async function collectAccountBatch(accounts, { collect, skip = () => "", concurrency = 2, changed = () => {} }) {
  const unique = [...new Map(accounts.map(account => [account.id, account])).values()];
  const rows = unique.map(account => ({ id: account.id, name: account.name, status: "queued", message: "等待采集" }));
  const publish = () => changed(rows.map(item => ({ ...item })));
  let cursor = 0;
  publish();
  async function worker() {
    while (cursor < unique.length) {
      const index = cursor++, account = unique[index];
      const reason = skip(account);
      if (reason) { rows[index] = { ...rows[index], status: "skipped", message: reason }; publish(); continue; }
      rows[index] = { ...rows[index], status: "running", message: "正在采集" }; publish();
      try {
        const result = await collect(account);
        rows[index] = { ...rows[index], status: result.status === "partial" ? "partial" : "success", count: result.submittable, message: result.status === "partial" ? "部分课程采集失败" : result.submittable + " 个待处理" };
      } catch (error) { rows[index] = { ...rows[index], status: "error", message: error.message || "采集失败" }; }
      publish();
    }
  }
  await Promise.all(Array.from({ length: Math.min(unique.length, Math.max(1, Math.min(8, Math.floor(Number(concurrency) || 2)))) }, worker));
  return rows;
}
