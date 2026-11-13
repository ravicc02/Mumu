export function createAccountAuthorization(api, { changed, completed, interval = 1500, schedule = setTimeout, unschedule = clearTimeout } = {}) {
  let generation = 0, timer = null, disposed = false;
  let state = { status: "idle", platform: "chaoxing", accountId: "", mode: "add", account: null, error: "", message: "" };
  const pending = () => ["opening", "waiting", "verifying"].includes(state.status);
  const update = fields => { state = { ...state, ...fields }; changed?.({ ...state }); };
  const current = token => !disposed && token === generation;
  const clear = () => { if (timer !== null) unschedule(timer); timer = null; };
  const fail = error => update({ status: "error", error: error.message || "授权失败，请重试。" });

  async function poll(token) {
    try {
      const result = await api.loginStatus(state.accountId);
      if (!current(token)) return;
      if (result.status === "success") {
        const sourceId = state.accountId;
        const accountId = result.account_id || state.accountId;
        update({ status: "verifying", accountId });
        const accounts = await api.accounts();
        if (!current(token)) return;
        const account = accounts.find(item => item.id === accountId);
        if (!account || account.status !== "ready") throw new Error("授权记录尚未保存，请重新授权。");
        if (account.platform === "chaoxing" && (!account.platform_user_id || !account.identity_verified_at)) {
          throw new Error("本地服务尚未同步账号资料，请更新并重启服务后重新授权。");
        }
        const message = state.mode === "add" ? accountId === sourceId ? "添加成功" : "授权已更新" : "授权成功";
        await completed?.(account, accounts, message);
        if (current(token)) update({ status: "success", account, message });
        return;
      }
      if (["error", "idle"].includes(result.status)) throw new Error(result.message || "授权会话已结束，请重新授权。");
      update({ status: result.status === "verifying" ? "verifying" : "waiting", message: result.message || "" });
      timer = schedule(() => { timer = null; void poll(token); }, interval);
    } catch (error) {
      if (current(token)) fail(error);
    }
  }

  async function start(platform, account = null, mode = account ? "update" : "add") {
    if (disposed || pending()) return false;
    clear();
    const token = ++generation;
    update({ status: "opening", platform, mode, accountId: account?.id || "", account, error: "", message: "" });
    try {
      const target = account || await api.createAccount("", platform);
      if (!current(token)) return false;
      update({ accountId: target.id, account: target });
      await api.login(target.id);
      if (!current(token)) return false;
      update({ status: "waiting" });
      timer = schedule(() => { timer = null; void poll(token); }, interval);
      return true;
    } catch (error) {
      if (current(token)) fail(error);
      return false;
    }
  }

  return {
    start,
    retry: () => start(state.platform, state.accountId ? { ...state.account, id: state.accountId } : null, state.mode),
    pending,
    dispose() { disposed = true; generation++; clear(); },
  };
}
