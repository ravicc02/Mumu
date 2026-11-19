<script setup>
import { computed, onMounted, ref } from "vue";
import { Check, Database, FolderOpen, HardDrive, LoaderCircle, RefreshCw, Trash2 } from "@lucide/vue";
import { api } from "../../../app/api.js";

const props = defineProps({
  info: { type: Object, default: () => ({}) },
  openData: { type: Function, required: true },
  openLogs: { type: Function, required: true },
});

const stats = ref(null);
const loading = ref(false);
const error = ref("");
const refreshedAt = ref("");
const optimizing = ref("");
const optimizeFeedback = ref("");
const backupLoading = ref(false);
const backupFeedback = ref("");
const backups = ref([]);
const backupOptions = ref({ include_accounts: true, include_artifacts: false, include_browser_profile: false });
const retention = ref({ log_retention_days: 14, log_size_mb: 200 });
const policy = ref({ frequency: "off", keep: 3 });
const saving = ref(false);
const restoring = ref("");
const retentionDays = computed(() => retention.value.log_retention_days);

async function loadDataPreferences() {
  try {
    const [preferences, backupPolicy] = await Promise.all([api.preferences(), api.backupPolicy()]);
    retention.value = { log_retention_days: preferences.system.log_retention_days, log_size_mb: preferences.system.log_size_mb };
    policy.value = backupPolicy;
  } catch (cause) { error.value = cause.message; }
}

async function saveDataPreferences() {
  saving.value = true;
  error.value = "";
  try {
    const preferences = await api.preferences();
    await api.savePreferences({ system: { ...preferences.system, ...retention.value } });
    policy.value = await api.saveBackupPolicy(policy.value);
    backupFeedback.value = "数据保留策略与自动备份设置已保存。";
  } catch (cause) { error.value = cause.message; }
  finally { saving.value = false; }
}

async function restoreBackup(backup) {
  if (!window.confirm(`将恢复 ${backup.id}。下次启动时覆盖数据库和备份中的账号文件，并生成恢复前快照（包含登录态，请勿外传）。未包含的本地文件保留。确定继续吗？`)) return;
  restoring.value = backup.id;
  error.value = "";
  try {
    await api.restoreDataBackup(backup.id);
    backupFeedback.value = "恢复请求已保存；请退出并重新启动应用。恢复会在后台任务启动前执行。";
  } catch (cause) { error.value = cause.message; }
  finally { restoring.value = ""; }
}

const optimizeItems = computed(() => {
  const breakdown = stats.value?.sizes?.accounts_breakdown || {};
  return [
    { scope: "workspaces", label: "已结束任务工作区", value: breakdown.tasks, detail: "删除超过保留期限且不属于活动任务的工作区" },
    { scope: "artifacts", label: "任务产物", value: breakdown.artifacts, detail: "删除超过保留期限且不属于活动任务的产物目录" },
    { scope: "logs", label: "日志归档", value: stats.value?.sizes?.logs, detail: "只处理 oldmain.log 与 archive/，不动今日日志" },
    { scope: "browser-cache", label: "浏览器缓存", value: breakdown.browser, detail: "只清理 Cache、Code Cache、GPU/Shader 缓存目录" },
  ];
});

const sizeItems = computed(() => {
  const sizes = stats.value?.sizes || {};
  return [
    { label: "数据库", value: sizes.database },
    { label: "账号数据", value: sizes.accounts },
    { label: "日志", value: sizes.logs },
    { label: "备份", value: sizes.backups },
  ];
});

const countItems = computed(() => {
  const counts = stats.value?.counts || {};
  return [
    { label: "账号", value: counts.accounts },
    { label: "作业", value: counts.assignments },
    { label: "任务", value: counts.tasks },
    { label: "网课记录", value: counts.learning_runs },
    { label: "任务产物", value: counts.artifacts },
  ];
});

const accountBreakdown = computed(() => {
  const values = stats.value?.sizes?.accounts_breakdown || {};
  return [
    { label: "浏览器", value: values.browser },
    { label: "任务工作区", value: values.tasks },
    { label: "任务产物", value: values.artifacts },
    { label: "签到素材", value: values.attendance },
  ];
});

function formatBytes(value) {
  const bytes = Number(value) || 0;
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KiB", "MiB", "GiB", "TiB"];
  let size = bytes;
  let index = -1;
  do { size /= 1024; index += 1; } while (size >= 1024 && index < units.length - 1);
  return `${size >= 100 ? size.toFixed(0) : size >= 10 ? size.toFixed(1) : size.toFixed(2)} ${units[index]}`;
}

function formatCount(value) {
  return new Intl.NumberFormat("zh-CN").format(Number(value) || 0);
}

async function refresh() {
  loading.value = true;
  error.value = "";
  try {
    stats.value = await api.dataStats();
    refreshedAt.value = new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  } catch (cause) {
    error.value = cause.message;
  } finally {
    loading.value = false;
  }
}

async function openDirectory(action) {
  error.value = "";
  try {
    const ok = await action();
    if (!ok) throw new Error("目录操作未完成，请检查本地服务与剪贴板权限。");
  } catch (cause) {
    error.value = cause.message;
  }
}

async function loadBackups() {
  try {
    backups.value = await api.dataBackups();
  } catch (cause) {
    error.value = cause.message;
  }
}

async function createBackup() {
  if (backupLoading.value) return;
  if (backupOptions.value.include_browser_profile && typeof window !== "undefined" && !window.confirm("浏览器 Profile 包含 Cookie 和本地登录态，打包或外传可能导致凭据泄露。确定继续吗？")) return;
  backupLoading.value = true;
  backupFeedback.value = "";
  error.value = "";
  try {
    const result = await api.createDataBackup(backupOptions.value);
    backupFeedback.value = `备份已创建：${result.id}，${formatBytes(result.bytes)}。`;
    await loadBackups();
  } catch (cause) {
    error.value = cause.message;
  } finally {
    backupLoading.value = false;
  }
}

async function optimize(item) {
  if (optimizing.value) return;
  const warning = item.scope === "browser-cache"
    ? "只会删除可再生缓存，不会删除 Cookie、Local Storage、IndexedDB 或登录态文件。"
    : `该操作会永久删除超过 ${retentionDays.value} 天且符合条件的数据，不能撤销。`;
  if (typeof window !== "undefined" && !window.confirm(`${item.label}：${warning}确定继续吗？`)) return;
  optimizing.value = item.scope;
  optimizeFeedback.value = "";
  error.value = "";
  try {
    const result = await api.optimizeData(item.scope, retentionDays.value);
    const skipped = Object.entries(result.skipped || {}).map(([reason, count]) => `${reason} ${count} 项`).join("；");
    optimizeFeedback.value = `${item.label}已完成，释放 ${formatBytes(result.released_bytes)}。${skipped ? `跳过：${skipped}` : ""}`;
    await refresh();
  } catch (cause) {
    error.value = cause.message;
  } finally {
    optimizing.value = "";
  }
}

onMounted(() => { refresh(); loadBackups(); loadDataPreferences(); });
</script>

<template>
  <div class="data-settings settings-section-stack">
    <section class="settings-card">
      <header class="settings-card-heading">
        <div class="settings-card-title">
          <h3>存储管理</h3>
          <p>按需刷新本机数据占用与业务记录数量。</p>
        </div>
        <button class="button light compact" type="button" :disabled="loading" @click="refresh">
          <LoaderCircle v-if="loading" class="spinning" :size="14" />
          <RefreshCw v-else :size="14" />
          刷新
        </button>
      </header>
      <div class="settings-card-body data-settings-body">
        <div v-if="loading && !stats" class="settings-loading" role="status">
          <LoaderCircle class="spinning" :size="18" />正在统计数据占用
        </div>
        <div v-else-if="error && !stats" class="settings-load-error" role="alert">{{ error }}</div>
        <template v-else>
          <div class="data-metric-grid">
            <div v-for="item in sizeItems" :key="item.label" class="data-metric">
              <span>{{ item.label }}</span>
              <strong>{{ formatBytes(item.value) }}</strong>
            </div>
          </div>
          <div class="data-metric-grid counts">
            <div v-for="item in countItems" :key="item.label" class="data-metric">
              <span>{{ item.label }}</span>
              <strong>{{ formatCount(item.value) }}</strong>
            </div>
          </div>
          <div class="data-breakdown">
            <span class="data-breakdown-label">账号数据</span>
            <span v-for="item in accountBreakdown" :key="item.label">{{ item.label }} {{ formatBytes(item.value) }}</span>
          </div>
          <p v-if="refreshedAt" class="settings-note">上次刷新 {{ refreshedAt }}。统计会忽略缓存目录，不读取浏览器内容。</p>
        </template>
      </div>
    </section>

    <section class="settings-card">
      <header class="settings-card-heading">
        <div class="settings-card-title">
          <h3>数据目录</h3>
          <p>数据库、账号资料、任务文件和日志均保存在本机。</p>
        </div>
        <HardDrive :size="16" class="data-card-icon" />
      </header>
      <div class="settings-card-body">
        <div class="settings-path-list">
          <div class="settings-path-row">
            <span class="settings-path-copy">
              <strong>数据根目录</strong>
              <code>{{ info?.data_root || "未获取" }}</code>
            </span>
            <button class="button light compact" type="button" @click="openDirectory(props.openData)"><FolderOpen :size="14" />打开</button>
          </div>
          <div class="settings-path-row">
            <span class="settings-path-copy">
              <strong>今日日志</strong>
              <code>{{ info?.main_log_path || (info?.logs_root ? `${info.logs_root}/main.log` : "未获取") }}</code>
            </span>
            <button class="button light compact" type="button" @click="openDirectory(props.openLogs)"><FolderOpen :size="14" />打开日志</button>
          </div>
        </div>
      </div>
    </section>

    <section class="settings-card">
      <header class="settings-card-heading">
        <div class="settings-card-title">
          <h3>资料备份</h3>
          <p>默认备份数据库、账号资料和登录态快照；不会包含密钥、日志或任务工作区。</p>
        </div>
        <Database :size="16" class="data-card-icon" />
      </header>
      <div class="settings-card-body data-backup-body">
        <label><input v-model="backupOptions.include_accounts" type="checkbox" />账号与偏好</label>
        <label><input v-model="backupOptions.include_artifacts" type="checkbox" />任务产物</label>
        <label class="data-sensitive-option"><input v-model="backupOptions.include_browser_profile" type="checkbox" />浏览器 Profile（含 Cookie）</label>
        <button class="button primary compact" type="button" :disabled="backupLoading" @click="createBackup">
          <LoaderCircle v-if="backupLoading" class="spinning" :size="14" />
          <Database v-else :size="14" />
          {{ backupLoading ? "正在创建" : "立即备份" }}
        </button>
        <p v-if="backupFeedback" class="settings-note" role="status"><Check :size="14" />{{ backupFeedback }}</p>
        <div v-if="backups.length" class="data-backup-list">
          <div v-for="backup in backups.slice(0, 3)" :key="backup.id" class="data-backup-item">
            <span>{{ backup.id }}</span>
            <span>{{ formatBytes(backup.bytes) }} · {{ backup.valid ? "校验通过" : "校验失败" }}</span>
            <button class="button light compact" type="button" :disabled="!backup.valid || Boolean(restoring)" @click="restoreBackup(backup)">{{ restoring === backup.id ? "正在安排" : "恢复" }}</button>
          </div>
        </div>
      </div>
    </section>

    <form class="settings-card" @submit.prevent="saveDataPreferences">
      <header class="settings-card-heading"><div class="settings-card-title"><h3>数据保留与自动备份</h3></div></header>
      <fieldset class="settings-card-body" :disabled="saving">
        <div class="settings-control-grid">
          <label class="settings-control-cell"><span>现场记录保留时间</span><input v-model.number="retention.log_retention_days" class="settings-number" type="number" required min="1" max="365" aria-label="现场记录保留天数" />天</label>
          <label class="settings-control-cell"><span>现场记录空间上限</span><input v-model.number="retention.log_size_mb" class="settings-number" type="number" required min="20" max="4096" aria-label="现场记录空间上限" />MiB</label>
          <label class="settings-control-cell"><span>自动备份</span><select v-model="policy.frequency" class="settings-select"><option value="off">关闭</option><option value="daily">每天</option><option value="weekly">每周</option></select></label>
          <label class="settings-control-cell"><span>自动备份保留份数</span><input v-model.number="policy.keep" class="settings-number" type="number" required min="1" max="10" /></label>
        </div>
        <p class="settings-note">自动备份仅在启动时检查周期，不含 Profile 和产物；只轮转自动备份，手动备份与恢复前快照保留。</p>
        <button class="button primary compact" type="submit">{{ saving ? "正在保存" : "保存设置" }}</button>
      </fieldset>
    </form>

    <section class="settings-card">
      <header class="settings-card-heading">
        <div class="settings-card-title">
          <h3>优化存储</h3>
          <p>按已保存的保留期限清理；浏览器缓存不受期限限制，今日日志会被保护。</p>
        </div>
        <Trash2 :size="16" class="data-card-icon" />
      </header>
      <div class="settings-card-body data-optimize-body">
        <div v-for="item in optimizeItems" :key="item.scope" class="data-optimize-row">
          <div class="data-optimize-copy">
            <strong>{{ item.label }}</strong>
            <span>{{ item.detail }}</span>
          </div>
          <span class="data-optimize-size">{{ formatBytes(item.value) }}</span>
          <button class="button light compact" type="button" :disabled="Boolean(optimizing) || !stats" @click="optimize(item)">
            <LoaderCircle v-if="optimizing === item.scope" class="spinning" :size="14" />
            <Trash2 v-else :size="14" />
            清理
          </button>
        </div>
        <p v-if="optimizeFeedback" class="settings-note" role="status"><Check :size="14" />{{ optimizeFeedback }}</p>
        <p class="settings-note">清理期间如果检测到任务、采集或授权正在运行，服务会返回 409 并保留全部数据。</p>
      </div>
    </section>

    <p v-if="error && stats" class="data-settings-error" role="alert">{{ error }}</p>
  </div>
</template>

<style scoped>
.data-settings-body { padding-bottom: 18px !important; }
.data-metric-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); border-bottom: 1px solid var(--line); }
.data-metric-grid.counts { grid-template-columns: repeat(5, minmax(0, 1fr)); border-bottom: 0; }
.data-metric { display: grid; gap: 7px; min-width: 0; padding: 18px 14px 16px 0; border-right: 1px solid var(--line); }
.data-metric + .data-metric { padding-left: 14px; }
.data-metric:last-child { border-right: 0; }
.data-metric span { color: var(--muted); font-size: 12px; }
.data-metric strong { color: var(--ink); font-size: 18px; font-weight: 500; font-variant-numeric: tabular-nums; }
.data-breakdown { display: flex; flex-wrap: wrap; gap: 8px 16px; padding: 13px 0 0; color: var(--muted); font-size: 11px; }
.data-breakdown-label { color: var(--ink); font-weight: 500; }
.data-card-icon { flex: none; color: var(--accent); }
.data-backup-body { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)) auto; align-items: center; gap: 12px 16px; padding-top: 16px; padding-bottom: 16px; }
.data-backup-body label { display: flex; align-items: center; gap: 7px; color: var(--ink); font-size: 12px; }
.data-backup-body input { accent-color: var(--accent); }
.data-sensitive-option { color: var(--red) !important; }
.data-backup-list { grid-column: 1 / -1; display: grid; gap: 7px; border-top: 1px solid var(--line); padding-top: 12px; }
.data-backup-item { display: flex; justify-content: space-between; gap: 12px; color: var(--muted); font-size: 11px; }
.data-backup-item span:first-child { color: var(--ink); font-variant-numeric: tabular-nums; }
.data-optimize-body { padding-top: 8px; padding-bottom: 12px; }
.data-optimize-row { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; align-items: center; gap: 16px; padding: 13px 0; border-bottom: 1px solid var(--line); }
.data-optimize-row:last-of-type { border-bottom: 0; }
.data-optimize-copy { display: grid; gap: 4px; min-width: 0; }
.data-optimize-copy strong { color: var(--ink); font-size: 13px; font-weight: 500; }
.data-optimize-copy span { color: var(--muted); font-size: 11px; line-height: 1.45; }
.data-optimize-size { color: var(--ink); font-size: 12px; font-variant-numeric: tabular-nums; white-space: nowrap; }
.data-settings-error { margin: 0; color: var(--red); font-size: 12px; }
.settings-note { display: flex; align-items: center; gap: 6px; }
@media (max-width: 820px) { .data-metric-grid, .data-metric-grid.counts { grid-template-columns: repeat(2, minmax(0, 1fr)); } .data-metric:nth-child(2n) { border-right: 0; } .data-metric:nth-child(n+3) { border-top: 1px solid var(--line); } .data-backup-body { grid-template-columns: repeat(2, minmax(0, 1fr)); } .data-backup-body button { justify-self: start; } .data-backup-list { grid-column: 1 / -1; } .data-optimize-row { grid-template-columns: minmax(0, 1fr) auto; } .data-optimize-row .data-optimize-size { grid-column: 2; grid-row: 1; } .data-optimize-row button { grid-column: 1 / -1; justify-self: start; } }
</style>
