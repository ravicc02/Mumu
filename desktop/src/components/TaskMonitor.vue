<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { ArrowUpRight, Check, CircleAlert, CloudOff, LoaderCircle, Pause, Play, RefreshCw, Waves, X } from "@lucide/vue";
import { newMonitorState, reconcileMonitor, restoreMonitorState, monitorSummary } from "../lib/task-monitor";
import { conciseMessage } from "../lib/task-presentation";
import WaterMascot from "./WaterMascot.vue";
import PlatformLogo from "./PlatformLogo.vue";

const props = defineProps({ tasks: { type: Array, default: () => [] }, accounts: { type: Array, default: () => [] }, ready: Boolean, online: Boolean, scope: String, updatedAt: Number, creating: Boolean, refresh: Function, inline: Boolean, hideTrigger: Boolean, host: Object, removedIds: { type: Array, default: () => [] } });
const emit = defineEmits(["open-task", "open-dashboard", "summary"]);
const root = ref(null), trigger = ref(null), closeButton = ref(null);
const opened = ref(false), refreshing = ref(false), refreshError = ref("");
const quiet = ref(false), tab = ref("round"), now = ref(Date.now());
const state = ref(newMonitorState());
let scope = "", timer, lastRefresh = 0, disposed = false;
const fresh = computed(() => props.online && Boolean(props.updatedAt) && now.value - props.updatedAt < 15000);
const summary = computed(() => monitorSummary(state.value, { online: fresh.value, ready: props.ready }));
watch(summary, value => emit("summary", value), { immediate: true });
const stateIcon = computed(() => !props.ready ? LoaderCircle : !fresh.value ? CloudOff : summary.value.mood === "happy" ? Check : summary.value.mood === "angry" ? CircleAlert : summary.value.paused && !summary.value.running ? Pause : Waves);
const attention = computed(() => props.tasks.filter(task => ["needs_intervention", "awaiting_user", "failed", "incomplete", "interrupted"].includes(task.status)
  || (task.status === "completed" && task.task_kind !== "learning" && task.submission_status !== "submitted_verified")));
const priority = task => ({ running: 0, stopping: 0, incomplete: 1, interrupted: 1, failed: 1, needs_intervention: 1, awaiting_user: 1, queued: 2, paused: 3, completed: 4, stopped: 5 })[task.status] ?? 6;
const visibleTasks = computed(() => [...(tab.value === "attention" ? attention.value : summary.value.entries)].sort((a, b) => priority(a) - priority(b)));
const partitionStyle = computed(() => ({ gridTemplateColumns: "repeat(" + (summary.value.total || 1) + ", minmax(0, 1fr))", columnGap: summary.value.total > 40 ? "1px" : summary.value.total > 24 ? "2px" : "4px" }));
const syncLabel = computed(() => props.updatedAt ? new Date(props.updatedAt).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }) : "等待同步");
const statusLabel = task => ({ queued: "排队", running: "执行中", paused: "已暂停", stopping: "停止中", stopped: "已停止", needs_intervention: task.task_kind === "learning" ? "待介入" : "待审核", awaiting_user: "待介入", incomplete: "未完成", failed: "中断", interrupted: "中断", completed: task.task_kind === "learning" || task.submission_status === "submitted_verified" ? "已完成" : "待核验" })[task.status] || "等待同步";
const statusClass = task => task.status === "completed" && statusLabel(task) === "已完成" ? "done" : ["failed", "interrupted", "incomplete"].includes(task.status) ? "error" : ["needs_intervention", "awaiting_user"].includes(task.status) || (task.status === "completed" && statusLabel(task) !== "已完成") ? "review" : task.status;
function observe() {
  if (!props.scope || !props.ready) return;
  const key = "studio:task-monitor:v1:" + props.scope;
  if (scope !== key) {
    scope = key;
    try { state.value = restoreMonitorState(JSON.parse(localStorage.getItem(key) || "null")); }
    catch { state.value = newMonitorState(); }
    try { quiet.value = localStorage.getItem("studio:mascot-motion") === "off"; } catch { /* Use device preference when storage is unavailable. */ }
  }
  state.value = reconcileMonitor(state.value, props.tasks, { ready: props.online, now: Date.now(), removedIds: props.removedIds });
  try { localStorage.setItem(key, JSON.stringify(state.value)); } catch { /* The active round remains in memory. */ }
}
watch(() => [props.inline, props.hideTrigger], () => { opened.value = false; });
watch(() => [props.tasks, props.scope, props.ready, props.online, props.removedIds], observe, { deep: true, immediate: true, flush: "post" });
async function setOpened(value) {
  opened.value = value;
  if (value) { tab.value = "round"; await nextTick(); closeButton.value?.focus({ preventScroll: true }); }
  else { await nextTick(); (trigger.value || document.querySelector(".town-mascot-marker"))?.focus({ preventScroll: true }); }
}
function outside(event) { if (!props.inline && opened.value && !root.value?.contains(event.target)) { opened.value = false; } }
function escape(event) { if (event.key === "Escape" && opened.value) { event.preventDefault(); event.stopPropagation(); setOpened(false); } }
function motion() {
  quiet.value = !quiet.value;
  try { localStorage.setItem("studio:mascot-motion", quiet.value ? "off" : "on"); } catch { /* The preference lasts for this window. */ }
}
async function refresh(manual = false) {
  if (refreshing.value || !props.refresh) return;
  refreshing.value = true; lastRefresh = Date.now();
  try { await props.refresh(); if (!disposed) refreshError.value = ""; }
  catch (error) { if (!disposed && manual) refreshError.value = conciseMessage(error.message); }
  finally { if (!disposed) refreshing.value = false; }
}
function openTask(task) {
  const current = props.tasks.find(item => item.id === task.id);
  if (!current) { refreshError.value = "任务记录已不可用，请刷新后查看"; return; }
  opened.value = false; trigger.value?.focus({ preventScroll: true }); emit("open-task", current);
}
function platform(task) { return task.platform || props.accounts.find(account => account.id === task.account_id)?.platform; }
defineExpose({ open: () => setOpened(true) });
onMounted(() => {
  document.addEventListener("pointerdown", outside);
  timer = setInterval(() => {
    now.value = Date.now();
    if (!document.hidden && props.online && (props.inline || opened.value || summary.value.busy || props.creating) && now.value - lastRefresh >= 2000) refresh();
  }, 1000);
});
onUnmounted(() => { disposed = true; clearInterval(timer); document.removeEventListener("pointerdown", outside); });
</script>

<template>
  <Teleport :to="host || 'body'" :disabled="!inline || !host">
  <div ref="root" class="task-monitor" :class="['mood-' + summary.mood, { 'is-open': opened, 'is-offline': !fresh, 'is-loading': !ready, 'is-inline': inline }]" @keydown="escape">
    <button v-if="!inline && !hideTrigger" ref="trigger" class="monitor-trigger" type="button" aria-haspopup="dialog" :aria-expanded="opened" aria-controls="task-monitor-space" :aria-label="'沐沐：' + summary.label + (summary.total ? '，完成 ' + summary.completed + '/' + summary.total : '')" :title="'沐沐 · ' + summary.label" @click="setOpened(!opened)">
      <WaterMascot :mood="summary.mood" :water="summary.water" :paused="quiet || !fresh" />
      <span class="monitor-trigger-copy"><strong>{{ summary.label }}</strong><small>{{ summary.total ? summary.completed + ' / ' + summary.total : '沐沐' }}</small></span>
      <span v-if="summary.attention" class="monitor-attention-dot" aria-hidden="true"></span>
    </button>
    <Transition name="monitor-space">
      <section v-if="inline || opened" id="task-monitor-space" class="monitor-space" :role="inline ? 'region' : 'dialog'" aria-labelledby="monitor-title" :data-state="summary.mood" :aria-busy="!ready">
        <header class="monitor-heading"><div><span class="monitor-monogram"><Waves :size="16" /></span><h2 id="monitor-title">沐沐</h2><span class="monitor-live-dot" :class="{ online: fresh }" :title="fresh ? '已连接任务状态' : '任务状态尚未同步'"></span></div><div class="monitor-tools"><button class="icon-button" type="button" :disabled="refreshing || !refresh" title="刷新任务状态" aria-label="刷新任务状态" @click="refresh(true)"><RefreshCw :size="14" :class="{ spinning: refreshing }" /></button><button class="icon-button" type="button" :title="quiet ? '恢复角色动效' : '暂停角色动效'" :aria-label="quiet ? '恢复角色动效' : '暂停角色动效'" :aria-pressed="quiet" @click="motion"><Play v-if="quiet" :size="14" /><Pause v-else :size="14" /></button><button v-if="!inline" ref="closeButton" class="icon-button" type="button" title="收起监控" aria-label="收起监控" @click="setOpened(false)"><X :size="15" /></button></div></header>
        <div class="monitor-stage"><span class="monitor-stage-kicker">值守舱</span><WaterMascot :mood="summary.mood" :water="summary.water" :total="summary.total" :paused="quiet || !fresh" detail /><span class="monitor-stage-id">{{ summary.total ? '本轮 ' + summary.total + ' 项' : '等待任务' }}</span></div>
        <div class="monitor-readout"><div><span class="monitor-current-state"><component :is="stateIcon" :size="14" :class="{ spinning: !ready }" />{{ summary.label }}</span><p>{{ summary.detail }}</p></div><div v-if="summary.total" class="monitor-fraction"><strong>{{ summary.completed }}<span>/ {{ summary.total }}</span></strong><small>{{ summary.percent }}%</small></div></div>
        <div v-if="summary.total" class="monitor-partitions" :style="partitionStyle" role="progressbar" aria-label="本轮任务完成比例" :aria-valuenow="summary.completed" :aria-valuemax="summary.total" aria-valuemin="0" :aria-valuetext="summary.completed + '/' + summary.total + ' 个任务完成'"><span v-for="task in summary.entries" :key="task.key" :class="statusClass(task)" :title="task.assignment_title + ' · ' + statusLabel(task)"></span></div>
        <div v-if="!inline" class="monitor-tally"><span><i class="done"></i>完成 <b>{{ summary.completed }}</b></span><span><i class="running"></i>处理中 <b>{{ summary.running + summary.queued }}</b></span><span><i class="review"></i>待介入 <b>{{ summary.review }}</b></span><span><i class="error"></i>异常 <b>{{ summary.errors }}</b></span></div>
        <div v-if="!inline" class="monitor-task-heading"><div role="tablist" aria-label="监控任务范围"><button type="button" role="tab" :aria-selected="tab === 'round'" aria-controls="monitor-task-list" @click="tab = 'round'">本轮任务<span>{{ summary.total }}</span></button><button type="button" role="tab" :aria-selected="tab === 'attention'" aria-controls="monitor-task-list" @click="tab = 'attention'">全部待处理<span>{{ attention.length }}</span></button></div></div>
        <div v-if="!inline" id="monitor-task-list" class="monitor-task-list" role="tabpanel" :aria-label="tab === 'round' ? '本轮任务' : '需处理任务'">
          <div v-if="!ready" class="monitor-empty"><LoaderCircle class="spinning" :size="16" />正在同步任务</div>
          <div v-else-if="!visibleTasks.length" class="monitor-empty"><Waves :size="20" /><span>{{ tab === 'round' ? '暂无本轮任务' : '暂无待处理任务' }}</span></div>
          <button v-for="task in visibleTasks" :key="task.key || task.task_key || task.id" type="button" class="monitor-task-row" :class="statusClass(task)" :aria-label="'查看任务：' + task.assignment_title" @click="openTask(task)"><PlatformLogo :platform="platform(task)" :size="23" /><span class="monitor-task-copy"><strong>{{ task.assignment_title }}</strong><small>{{ task.account_name }}<span v-if="task.error"> · {{ conciseMessage(task.error, { limit: 52 }) }}</span><span v-else> · {{ task.task_kind === 'learning' ? '网课' : task.course_name }}</span></small></span><span class="monitor-task-status">{{ statusLabel(task) }}</span><ArrowUpRight :size="13" /></button>
        </div>
        <p v-if="refreshError" class="monitor-refresh-error" role="alert">{{ refreshError }}</p>
        <footer class="monitor-footer"><span><span class="monitor-live-dot" :class="{ online: fresh }"></span>{{ fresh ? '同步于 ' + syncLabel : '等待重新连接' }}</span><button v-if="!inline" class="text-button" type="button" @click="opened = false; emit('open-dashboard')">任务面板<ArrowUpRight :size="13" /></button></footer>
      </section>
    </Transition>
  </div>
  </Teleport>
</template>
