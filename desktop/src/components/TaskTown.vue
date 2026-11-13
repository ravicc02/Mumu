<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { Search, X, RotateCcw, ZoomIn, ZoomOut, Pause, Play, Gamepad2, FileText, Trash2, LoaderCircle, ArrowUpRight, Users } from "@lucide/vue";
import PlatformLogo from "./PlatformLogo.vue";
import { TOWN_DISTRICTS, townDistrict, townTaskKey, townTaskActions } from "../lib/task-town.js";
import { conciseMessage } from "../lib/task-presentation.js";
import { COMPANY_ROOMS, companyRoomForTask, companyLiveTask } from "../lib/task-company.js";
import { createCompanyScreenFeed } from "../lib/task-company-screen.js";
import { api } from "../api.js";

const props = defineProps({
  tasks: { type: Array, default: () => [] }, accounts: { type: Array, default: () => [] },
  monitor: { type: Object, default: null },
  ready: Boolean, online: Boolean,
});
const emit = defineEmits(["details", "delete", "create", "open-monitor"]);
const canvas = ref(null), stage = ref(null), selectedCard = ref(null), hoverCard = ref(null);
const mascotMarker = ref(null), interactMarker = ref(null), nearbyKey = ref(null);
const loading = ref(true), graphicsError = ref(""), selectedKey = ref(null), hoveredKey = ref(null);
const query = ref(""), accountFilter = ref(""), roaming = ref(true), quiet = ref(false);
const currentRoom = ref('office'), interaction = ref(null), bossMarker = ref(null), liveKey = ref(''), streamState = ref('idle');
const walkState = ref('idle');
const liveCandidates = computed(() => props.tasks.filter(task => task.status === 'running' && task.browser_available === true));
const liveTask = computed(() => companyLiveTask(props.tasks, liveKey.value));
const rooms = computed(() => COMPANY_ROOMS.map(room => ({ ...room, count: filtered.value.filter(task => companyRoomForTask(task) === room.id).length })));
const botElements = new Map();
let controller, screenFeed, mounted = false, bootGeneration = 0;
const filtered = computed(() => props.tasks.filter(task => {
  const search = query.value.trim().toLocaleLowerCase();
  return (!accountFilter.value || task.account_id === accountFilter.value)
    && (!search || [task.assignment_title, task.course_name, task.account_name].join(" ").toLocaleLowerCase().includes(search));
}));
const selected = computed(() => props.tasks.find(task => townTaskKey(task) === selectedKey.value));
const hovered = computed(() => filtered.value.find(task => townTaskKey(task) === hoveredKey.value));
const nearby = computed(() => filtered.value.find(task => townTaskKey(task) === nearbyKey.value));
const actions = computed(() => selected.value ? townTaskActions(selected.value) : {});
const currentDistrict = computed(() => selected.value && TOWN_DISTRICTS.find(district => district.id === townDistrict(selected.value)));
const taskPlatform = task => task.platform || props.accounts.find(account => account.id === task.account_id)?.platform || "chaoxing";
const statusText = task => task.status === "stopped" ? "已停止" : task.status === "queued" ? "排队中" : task.status === "stopping" ? "停止中" : TOWN_DISTRICTS.find(district => district.id === townDistrict(task))?.label;
const resultText = task => conciseMessage(task.error || task.current_action || task.phase_label || task.result_summary || "", { limit: 100 });

function position(element, point, offsetY = 0) {
  if (!element) return;
  element.style.transform = "translate3d(" + point.x.toFixed(1) + "px," + (point.y + offsetY).toFixed(1) + "px,0) translate(-50%,-50%)";
  element.style.visibility = point.visible ? "visible" : "hidden";
}
function frame(data) {
  position(mascotMarker.value, data.mascot);
  if (data.boss) position(bossMarker.value, data.boss);
  if (canvas.value && data.player) {
    canvas.value.dataset.playerX = data.player.x.toFixed(2); canvas.value.dataset.playerZ = data.player.z.toFixed(2);
    canvas.value.dataset.playerY = data.player.y.toFixed(3); canvas.value.dataset.grounded = String(data.player.grounded);
    canvas.value.dataset.room = data.room; canvas.value.dataset.cameraMode = data.overview ? 'overview' : 'third-person';
    canvas.value.dataset.screenFrames = String(data.frameNumber); canvas.value.dataset.seated = String(data.seated);
    canvas.value.dataset.walkState = data.navigation.state;
    if (data.navigation.target) {
      canvas.value.dataset.targetX = data.navigation.target.x.toFixed(2); canvas.value.dataset.targetZ = data.navigation.target.z.toFixed(2);
    } else { delete canvas.value.dataset.targetX; delete canvas.value.dataset.targetZ; }
  }
  const interact = data.bots.find(point => point.key === data.nearby);
  if (interact) position(interactMarker.value, interact, -42);
  for (const point of data.bots) {
    const element = botElements.get(point.key);
    position(element, point);
    if (element) {
      element.style.width = point.width.toFixed(1) + "px"; element.style.height = point.height.toFixed(1) + "px";
      element.style.zIndex = String(Math.round(1000 - point.depth * 300));
    }
  }
  const hover = data.bots.find(point => point.key === hoveredKey.value);
  if (hover && hoverCard.value) {
    const bounds = hoverCard.value.getBoundingClientRect();
    position(hoverCard.value, { ...hover,
      x: Math.max(bounds.width / 2 + 10, Math.min(hover.x, data.width - bounds.width / 2 - 10)),
      y: Math.max(bounds.height / 2 + 10, hover.y - 53),
    });
  }
  const point = data.bots.find(item => item.key === selectedKey.value);
  if (point && selectedCard.value) {
    const bounds = selectedCard.value.getBoundingClientRect();
    const above = point.y - bounds.height - 48 >= 16;
    const left = Math.max(16, Math.min(above ? point.x - bounds.width / 2 : point.x + 60, data.width - bounds.width - 16));
    const top = Math.max(16, Math.min(above ? point.y - bounds.height - 48 : point.y - bounds.height / 2, data.height - bounds.height - 20));
    selectedCard.value.dataset.placement = above ? "above" : "side";
    selectedCard.value.style.setProperty("--pointer-left", Math.max(20, Math.min(point.x - left, bounds.width - 20)) + "px");
    selectedCard.value.style.transform = "translate3d(" + left.toFixed(1) + "px," + top.toFixed(1) + "px,0)";
  }
}
function synchronize() {
  controller?.setTasks(props.tasks, new Set(filtered.value.map(townTaskKey)));
  const completed = props.tasks.filter(task => townDistrict(task) === "completed").length;
  controller?.setWater(props.monitor?.water ?? (props.tasks.length ? completed / props.tasks.length : .12));
  controller?.setMood(props.monitor?.mood || "idle");
  screenFeed?.select(props.online ? liveTask.value : null);
  if (selectedKey.value && !filtered.value.some(task => townTaskKey(task) === selectedKey.value)) reset();
}
async function boot() {
  const generation = ++bootGeneration;
  screenFeed?.dispose(); controller?.dispose(); controller = null;
  loading.value = true; graphicsError.value = "";
  try {
    const { createTaskTown } = await import("../lib/task-company-scene.js");
    if (!mounted || generation !== bootGeneration) return;
    controller = createTaskTown(canvas.value, {
      frame, selection: selectTask, nearby: key => { nearbyKey.value = key; },
      monitor: () => emit("open-monitor"),
      interaction: value => { interaction.value = value; }, room: value => { currentRoom.value = value; },
      navigation: value => { walkState.value = value; },
      screen: () => { if (liveTask.value) emit('details', liveTask.value); },
      roaming: value => { roaming.value = value; },
      error: message => { graphicsError.value = message; screenFeed?.dispose(); controller?.dispose(); controller = null; },
    });
    screenFeed = createCompanyScreenFeed(api, {
      status: (task, state) => { streamState.value = state; controller?.screenStatus(task, state); },
      frame: payload => { streamState.value = 'connected'; controller?.browserFrame(payload); },
    });
    synchronize();
  } catch (error) {
    if (mounted) graphicsError.value = "当前设备无法启动 3D 画面。任务与操作仍可使用。";
    console.error("Task town initialization failed", error);
  } finally { if (mounted && generation === bootGeneration) loading.value = false; }
}
async function selectTask(key) {
  selectedKey.value = key; hoveredKey.value = null;
  if (key) { await nextTick(); selectedCard.value?.focus({ preventScroll: true }); }
}
function choose(task) { if (controller) controller.focus(townTaskKey(task)); else selectTask(townTaskKey(task)); }
function reset() { selectedKey.value = null; hoveredKey.value = null; controller?.reset(); }
function closeCard() { selectedKey.value = null; hoveredKey.value = null; controller?.setRoaming(true); canvas.value?.focus({ preventScroll: true }); }
function visitRoom(id) { controller?.visitRoom(id); canvas.value?.focus({ preventScroll: true }); }
function hover(task) { hoveredKey.value = task ? townTaskKey(task) : null; controller?.hover(hoveredKey.value); }
function toggleMotion() { quiet.value = !quiet.value; controller?.setPaused(quiet.value); }
function toggleRoaming() { controller?.setRoaming(!roaming.value); }
function zoom(direction) { controller?.zoom(direction); }
function register(map, key, element) { if (element) map.set(key, element); else map.delete(key); }
watch(() => props.tasks, synchronize, { deep: true });
watch(filtered, synchronize);
watch(() => [liveTask.value?.id, liveTask.value?.task_kind, props.online], () => screenFeed?.select(props.online ? liveTask.value : null));
watch(() => props.monitor, () => { controller?.setWater(props.monitor?.water ?? .12); controller?.setMood(props.monitor?.mood || "idle"); });
onMounted(() => { mounted = true; boot(); });
onUnmounted(() => { mounted = false; bootGeneration++; screenFeed?.dispose(); controller?.dispose(); botElements.clear(); });
</script>

<template>
  <section class="task-town company-workspace" aria-label="任务公司">
    <div class="town-toolbar">
      <div class="town-filters">
        <label class="town-search"><Search :size="15" /><input v-model="query" aria-label="搜索公司任务" placeholder="搜索任务、课程、账号"><button v-if="query" class="icon-button" type="button" title="清除搜索" aria-label="清除搜索" @click="query = ''"><X :size="13" /></button></label>
        <select v-model="accountFilter" aria-label="筛选公司账号"><option value="">全部账号</option><option v-for="account in accounts" :key="account.id" :value="account.id">{{ account.name }}</option></select>
      </div>
      <div class="town-tools">
        <span class="town-connection" :class="{ offline: !online }"><i></i>{{ online ? '实时同步' : '连接中断' }}</span>
        <button class="icon-button" type="button" title="全景视角" aria-label="全景视角" @click="reset"><RotateCcw :size="16" /></button>
        <button class="icon-button" type="button" title="拉近视角" aria-label="拉近视角" :disabled="!!graphicsError" @click="zoom(1)"><ZoomIn :size="16" /></button>
        <button class="icon-button" type="button" title="拉远视角" aria-label="拉远视角" :disabled="!!graphicsError" @click="zoom(-1)"><ZoomOut :size="16" /></button>
        <button class="icon-button" :class="{ 'is-on': roaming }" type="button" title="沐沐漫游（WASD / 方向键）" aria-label="沐沐漫游" :aria-pressed="roaming" :disabled="!!graphicsError" @click="toggleRoaming"><Gamepad2 :size="17" /></button>
        <button class="icon-button" type="button" :title="quiet ? '恢复场景动效' : '暂停场景动效'" :aria-label="quiet ? '恢复场景动效' : '暂停场景动效'" :aria-pressed="quiet" @click="toggleMotion"><Play v-if="quiet" :size="15" /><Pause v-else :size="15" /></button>
      </div>
    </div>
    <div ref="stage" class="town-stage" :class="{ 'has-selection': selected, 'is-roaming': roaming }">
      <canvas ref="canvas" class="town-canvas" tabindex="0" role="application" aria-label="任务公司 3D 场景" aria-description="点击地面自动寻路，WASD 或方向键随时接管移动，空格跳跃。拖动转向，滚轮缩放，靠近角色或老板办公桌按 F 交互。"></canvas>
      <div v-if="walkState === 'blocked'" class="company-navigation-feedback" role="status">该位置无法到达</div>
      <div v-if="loading || !ready" class="town-loading" role="status"><LoaderCircle :size="22" class="spin" /><span>{{ loading ? '正在进入公司' : '正在同步任务' }}</span></div>
      <div v-if="!loading && !graphicsError" class="town-overlay">
        <button v-if="!selected && ['boss', 'stand'].includes(interaction)" ref="bossMarker" class="company-boss-interact" type="button" :aria-label="interaction === 'stand' ? '离开老板座位' : '坐下查看总状况'" aria-keyshortcuts="F" @click="controller?.interact(); canvas?.focus({ preventScroll: true })"><kbd>F</kbd>{{ interaction === 'stand' ? '起身' : '入座 · 总览' }}</button>
        <div v-if="liveCandidates.length" class="company-screen-picker"><select v-model="liveKey" aria-label="公司大屏任务"><option value="">当前运行任务</option><option v-for="task in liveCandidates" :key="townTaskKey(task)" :value="townTaskKey(task)">{{ task.account_name }} · {{ task.assignment_title }}</option></select><button class="icon-button" type="button" title="重新连接公司大屏" aria-label="重新连接公司大屏" @click="screenFeed?.select(liveTask, true)"><RotateCcw :size="14" /></button><i :class="streamState" :title="streamState === 'connected' ? '大屏实时连接' : '大屏' + (streamState === 'connecting' ? '连接中' : '已断开')"></i></div>
        <button ref="mascotMarker" class="town-mascot-marker" type="button" :aria-label="'沐沐：' + (monitor?.label || '任务监控')" title="查看沐沐任务详情" aria-haspopup="dialog" aria-controls="task-monitor-space" @click="emit('open-monitor')"></button>
        <nav class="company-room-nav" aria-label="公司房间"><button v-for="room in rooms" :key="room.id" type="button" :class="{ active: currentRoom === room.id }" :style="{ '--room-color': room.color }" :aria-label="'前往' + room.label" :aria-current="currentRoom === room.id ? 'location' : undefined" @click="visitRoom(room.id)"><i></i>{{ room.label }}<span v-if="room.id !== 'boss'">{{ room.count }}</span></button></nav>
        <button v-for="task in filtered" :key="townTaskKey(task)" :ref="element => register(botElements, townTaskKey(task), element)" :id="'town-task-' + task.id" type="button" class="town-bot-marker" :class="{ selected: selectedKey === townTaskKey(task), 'is-nearby': nearbyKey === townTaskKey(task) }" :data-task-id="task.id" :data-district="townDistrict(task)" :data-key="townTaskKey(task)" :aria-label="task.assignment_title + '，' + task.account_name + '，' + statusText(task)" :aria-expanded="selectedKey === townTaskKey(task)" :aria-keyshortcuts="nearbyKey === townTaskKey(task) ? 'F' : undefined" aria-controls="town-task-card" @pointerenter="hover(task)" @pointerleave="hover(null)" @focus="hover(task)" @blur="hover(null)" @click.stop="choose(task)"><span class="town-focus-ring"></span></button>
        <Transition name="town-tooltip"><button v-if="nearby && !selected && interaction === 'task'" ref="interactMarker" class="town-interact" type="button" :title="'查看任务：' + nearby.assignment_title + '（F）'" aria-label="查看附近任务" aria-keyshortcuts="F" @click.stop="choose(nearby)"><kbd>F</kbd></button></Transition>
        <Transition name="town-tooltip"><div v-if="hovered && !selected" ref="hoverCard" class="town-hover-card" role="tooltip"><strong>{{ hovered.assignment_title }}</strong><span>{{ hovered.account_name }}</span></div></Transition>
      </div>
      <Transition name="town-card">
        <section v-if="selected" id="town-task-card" ref="selectedCard" class="town-task-card" role="dialog" aria-label="任务操作" tabindex="-1" :style="{ '--district-color': currentDistrict?.color }" @keydown.esc.stop="closeCard">
          <div class="town-card-top"><PlatformLogo :platform="taskPlatform(selected)" :size="26" /><span>{{ selected.account_name }}<small>{{ selected.task_kind === 'learning' ? '网课' : selected.course_name }}</small></span><button class="icon-button" type="button" title="收起任务" aria-label="收起任务" @click="closeCard"><X :size="16" /></button></div>
          <h3>{{ selected.assignment_title }}</h3>
          <div class="town-card-status"><span><i></i>{{ statusText(selected) }}</span><small v-if="selected.attempt">第 {{ selected.attempt }} 次执行</small></div>
          <p v-if="resultText(selected)" class="town-card-result">{{ resultText(selected) }}</p>
          <div v-if="['running', 'queued', 'needs_intervention', 'completed'].includes(selected.status)" class="town-task-progress"><span :style="{ width: Math.max(0, Math.min(100, selected.progress || (selected.status === 'completed' ? 100 : 0))) + '%' }"></span></div>
          <div class="town-card-actions"><button class="button dark" type="button" @click="emit('details', selected)"><FileText :size="14" />{{ actions.detailsLabel }}<ArrowUpRight :size="14" /></button><button class="icon-button town-delete" type="button" :disabled="!actions.deletable" :title="actions.deletable ? '删除任务' : '请先停止任务并关闭任务浏览器'" aria-label="删除任务" @click="emit('delete', selected)"><Trash2 :size="16" /></button></div>
        </section>
      </Transition>
      <div v-if="graphicsError" class="town-graphics-error" role="status"><p>{{ graphicsError }}</p><button class="button light compact" type="button" @click="boot"><RotateCcw :size="14" />重新加载</button><div class="town-fallback-tasks"><button v-for="task in filtered" :key="townTaskKey(task)" type="button" @click="choose(task)">{{ task.assignment_title }}<ArrowUpRight :size="14" /></button></div></div>
      <div v-if="ready && !loading && !filtered.length && !graphicsError" class="town-empty"><span>{{ tasks.length ? '没有匹配的任务' : '暂无任务' }}</span><button v-if="tasks.length" class="button light compact" type="button" @click="query = ''; accountFilter = ''">清除筛选</button><button v-else class="button dark compact" type="button" @click="emit('create')">执行作业<ArrowUpRight :size="14" /></button></div>
      <div class="town-corner-count"><Users :size="13" /><span>{{ filtered.length }} 个任务</span></div>
    </div>
  </section>
</template>

<style src="../task-town.css"></style>
