<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { BookOpen, CheckCheck, CircleAlert, ExternalLink, Image, LoaderCircle, Maximize2, MonitorOff, Pause, Play, RefreshCw, RotateCcw, Search, Square, Plus, X } from "@lucide/vue";
import { api } from "../../../app/api.js";
import { mergeJobState, reconcileEvents } from "../../tasks/lib/state.js";
import PlatformLogo from "../../platforms/components/PlatformLogo.vue";
import ScreenshotViewer from "../../tasks/components/ScreenshotViewer.vue";
import LiveBrowser from "../../tasks/components/LiveBrowser.vue";
import { conciseMessage } from "../../tasks/lib/presentation.js";
import { platformById, accountsForBusiness, businessAccountId, recordsForAccount } from "../../platforms/lib/catalog.js";
import { learningScreenshotSections } from "../lib/result.js";

const props = defineProps({ accounts: { type: Array, default: () => [] }, accountId: { type: String, default: "" }, initialCourseId: { type: String, default: "" } });
const emit = defineEmits(["update:accountId", "refresh", "result", "platforms"]);
const year = ref(2026);
const addCourseOpen = ref(false), addingCourse = ref(false), courseForm = ref({name:"",url:""}), addError = ref("");
const query = ref("");
const courses = ref([]);
const runs = ref([]);
const selectedCourse = ref("");
const selectedRun = ref("");
const events = ref([]);
const artifacts = ref([]);
const image = ref("");
const liveViewer = ref(null);
const collecting = ref({});
const pending = ref({});
const message = ref("");
const error = ref("");
let generation = 0, timer = null, disposed = false;
const active = new Set(["queued", "running", "paused", "awaiting_user", "stopping"]);
const learningAccounts = computed(() => accountsForBusiness(props.accounts, "learning"));
const account = computed(() => learningAccounts.value.find(item => item.id === props.accountId));
const yearFilter = computed(() => Boolean(platformById[account.value?.platform]?.academic_year_filter));
const scopeYear = computed(() => yearFilter.value ? year.value : 0);
const course = computed(() => recordsForAccount(courses.value, account.value?.id).find(item => item.id === selectedCourse.value));
const run = computed(() => recordsForAccount(runs.value, account.value?.id).find(item => item.id === selectedRun.value));
const filtered = computed(() => recordsForAccount(courses.value, account.value?.id).filter(item => item.name.toLowerCase().includes(query.value.toLowerCase())));
const sections = computed(() => learningScreenshotSections(artifacts.value, api.learningArtifactUrl));
const statusLabel = status => ({ queued: "排队中", running: "运行中", paused: "已暂停", awaiting_user: "待人工处理", stopping: "停止中", stopped: "已停止", interrupted: "中断", incomplete: "未完成", completed: "已完成" })[status] || "未执行";
const clock = seconds => `${Math.floor((seconds || 0) / 60)}:${String(Math.floor((seconds || 0) % 60)).padStart(2, "0")}`;
const latest = id => runs.value.find(item => item.course_id === id);
const duplicateName = item => courses.value.filter(other => other.name === item.name).length > 1;
const logMessage = event => conciseMessage(event.message, { kind: event.kind });

async function loadCourses(token = generation) {
  const accountId = props.accountId;
  if (!account.value) return;
  const result = await api.learningCourses(accountId, scopeYear.value);
  if (token !== generation || accountId !== props.accountId || disposed) return;
  courses.value = recordsForAccount(result, accountId);
  if (!courses.value.some(item => item.id === selectedCourse.value)) selectedCourse.value = courses.value.find(item => item.id === account.value?.learning_run?.course_id)?.id || courses.value[0]?.id || "";
}

function selectCourse(item) {
  if (!account.value || item.account_id !== account.value.id) return;
  error.value = "";
  selectedCourse.value = item.id;
  selectedRun.value = latest(item.id)?.id || "";
  events.value = []; artifacts.value = []; image.value = "";
  refresh();
}

async function addCourse() {
  if (!account.value || addingCourse.value) return;
  addingCourse.value = true; addError.value = "";
  const accountId = props.accountId, token = generation;
  try {
    const added = await api.addLearningCourse({account_id:accountId,name:courseForm.value.name.trim(),url:courseForm.value.url.trim()});
    if (token !== generation || accountId !== props.accountId) return;
    await loadCourses(); selectedCourse.value=added.id; selectedRun.value="";
    addCourseOpen.value=false; courseForm.value={name:"",url:""};
  } catch(failure) { addError.value=conciseMessage(failure.message); }
  finally { addingCourse.value=false; }
}

async function collect() {
  const accountId = props.accountId;
  if (!account.value || collecting.value[accountId]) return;
  const accountName = account.value?.name || "账号";
  const academicYear = scopeYear.value;
  collecting.value[accountId] = true;
  error.value = "";
  try {
    const result = await api.collectLearningCourses(accountId, academicYear);
    if (props.accountId === accountId && scopeYear.value === academicYear) {
      message.value = `${accountName}：已采集 ${result.count} 门课程`;
      await loadCourses();
    }
  } catch (failure) { if (props.accountId === accountId && scopeYear.value === academicYear) error.value = failure.message; }
  finally { delete collecting.value[accountId]; emit("refresh"); }
}

async function execute(item) {
  if (!account.value || item.account_id !== account.value.id) return;
  const existing = latest(item.id);
  if (existing && active.has(existing.status)) { selectCourse(item); return; }
  if (pending.value[item.id]) return;
  pending.value[item.id] = true; error.value = "";
  try {
    const created = await api.createLearningRun(item.id);
    runs.value = [created, ...runs.value];
    if (props.accountId === created.account_id) { selectedCourse.value = item.id; selectedRun.value = created.id; events.value = []; artifacts.value = []; image.value = ""; }
    emit("refresh");
  } catch (failure) { error.value = failure.message; }
  finally { delete pending.value[item.id]; }
}

async function control(action) {
  const identifier = selectedRun.value;
  if (!run.value || !identifier || pending.value[identifier]) return;
  pending.value[identifier] = true; error.value = "";
  try {
    const result = await api.controlLearningRun(identifier, action);
    runs.value = [result, ...runs.value.filter(item => item.id !== result.id)];
    if (selectedRun.value === identifier) {
      selectedRun.value = result.id;
      if (result.id !== identifier) { events.value = []; artifacts.value = []; image.value = ""; }
    }
    emit("refresh");
    await refresh();
  } catch (failure) { error.value = failure.message; }
  finally { delete pending.value[identifier]; }
}

async function refresh() {
  const token = generation, accountId = props.accountId;
  if (!account.value || disposed) return;
  try {
    const result = await api.learningRuns(accountId);
    if (disposed || token !== generation || accountId !== props.accountId) return;
    runs.value = recordsForAccount(result, accountId).map(item => mergeJobState(runs.value.find(previous => previous.id === item.id), item));
    if (!selectedRun.value && selectedCourse.value) selectedRun.value = latest(selectedCourse.value)?.id || "";
    const identifier = selectedRun.value;
    if (!identifier) return;
    const current = result.find(item => item.id === identifier);
    const cursor = Math.max(0, ...events.value.map(item => item.id));
    const [newEvents, evidence] = await Promise.all([api.learningEvents(identifier, cursor), api.learningArtifacts(identifier)]);
    if (token !== generation || selectedRun.value !== identifier || disposed) return;
    events.value = reconcileEvents([...events.value, ...newEvents]);
    artifacts.value = evidence;
    image.value = !current?.browser_available ? sections.value[0]?.src || "" : "";
  } catch (failure) { if (token === generation && !disposed) error.value = failure.message; }
}

async function reset() {
  const token = ++generation;
  query.value = ""; courses.value = []; runs.value = []; events.value = []; artifacts.value = []; image.value = "";
  selectedCourse.value = props.initialCourseId; selectedRun.value = ""; error.value = "";
  message.value = ""; addError.value = ""; addCourseOpen.value = false;
  if (!account.value) { selectedCourse.value = ""; return; }
  try { await loadCourses(token); if (token === generation) await refresh(); }
  catch (failure) { if (token === generation) error.value = failure.message; }
}
async function tick() { await refresh(); if (!disposed) timer = setTimeout(tick, 1500); }
watch([() => props.accountId, () => account.value?.platform, year], reset, { flush: "sync" });
watch(() => [props.accountId, learningAccounts.value], () => {
  const next = businessAccountId(props.accounts, "learning", props.accountId);
  if (next !== props.accountId) emit("update:accountId", next);
}, { immediate: true });
onMounted(async () => { await reset(); if (!disposed) timer = setTimeout(tick, 1500); });
onUnmounted(() => { disposed = true; generation++; clearTimeout(timer); });
</script>

<template>
  <main class="learning-workspace">
    <section class="learning-courses">
      <div class="learning-title"><BookOpen :size="20" /><h2>网课</h2><span class="count">{{ courses.length }}</span></div>
      <div class="learning-filters">
        <select :value="accountId" aria-label="网课账号" :disabled="!learningAccounts.length" @change="emit('update:accountId', $event.target.value)"><option v-if="!learningAccounts.length" value="">暂无网课账号</option><option v-for="item in learningAccounts" :key="item.id" :value="item.id">{{ item.name }} · {{ platformById[item.platform]?.label }}</option></select>
        <input v-if="yearFilter" v-model.number="year" type="number" min="2000" max="2100" aria-label="网课开课年份">
        <button class="button dark" type="button" :disabled="!account || account.status !== 'ready' || collecting[accountId]" @click="collect"><LoaderCircle v-if="collecting[accountId]" class="spinning" :size="14" /><RefreshCw v-else :size="14" />{{ collecting[accountId] ? '采集中' : '采集课程' }}</button>
      </div>
      <button v-if="account && !yearFilter" class="button light" type="button" @click="addCourseOpen=true"><Plus :size="14" />加入课程</button>
      <label class="learning-search"><Search :size="14" /><input v-model="query" aria-label="搜索网课" placeholder="搜索课程"></label>
      <div v-if="message" class="learning-message" role="status">{{ message }}</div>
      <div class="learning-course-list">
        <article v-for="item in filtered" :key="item.id" :class="{ selected: selectedCourse === item.id }">
          <button class="learning-course-select" type="button" @click="selectCourse(item)"><PlatformLogo :platform="item.platform" :size="29" /><span><strong>{{ item.name }}</strong><small>{{ item.semester }}<template v-if="duplicateName(item)"> · 班级 {{ item.class_id }}</template><template v-if="latest(item.id)"> · {{ statusLabel(latest(item.id).status) }}</template></small></span></button>
          <button class="icon-button" type="button" :title="latest(item.id) && active.has(latest(item.id).status) ? '查看任务' : '执行课程'" :aria-label="`执行 ${item.name}（班级 ${item.class_id}）`" :disabled="pending[item.id]" @click="execute(item)"><Play :size="15" /></button>
        </article>
        <div v-if="!filtered.length" class="empty-state"><BookOpen :size="26" /><h3>{{ !learningAccounts.length ? '暂无支持网课的账号' : collecting[accountId] ? '正在采集课程' : '暂无课程' }}</h3><button v-if="!learningAccounts.length" class="button light" type="button" @click="emit('platforms')">管理平台账号</button></div>
      </div>
    </section>
    <section class="learning-detail">
      <header class="learning-run-heading"><div><span class="panel-label"><PlatformLogo :platform="account?.platform" :size="19" />{{ account?.name }}<span v-if="run?.auto_retry_count" class="learning-retry-count">自动重试 {{ run.auto_retry_count }}/3</span></span><h2>{{ run?.course_name || course?.name || '选择课程' }}</h2></div><span v-if="run?.browser_available" class="learning-browser-mode">{{ run.headless ? '后台运行' : '窗口运行' }}</span><span v-if="run" class="job-badge" :class="run.status">{{ statusLabel(run.status) }}</span></header>
      <div v-if="error" class="learning-error" role="alert"><CircleAlert :size="16" />{{ error }}</div>
      <div class="learning-controls">
        <button v-if="course && !run" class="button dark" type="button" :disabled="pending[course.id]" @click="execute(course)"><Play :size="14" />执行</button>
        <button v-if="run?.actions.includes('view_browser')" class="button dark" type="button" @click="liveViewer?.expand()"><Maximize2 :size="14" />实时操控</button>
        <button v-if="run?.actions.includes('open_browser')" class="button dark" type="button" :disabled="pending[run.id]" @click="control('open_browser')"><ExternalLink :size="14" />进入浏览器</button>
        <button v-if="run?.actions.includes('pause')" class="button light" type="button" :disabled="pending[run.id]" @click="control('pause')"><Pause :size="14" />暂停</button>
        <button v-if="run?.actions.includes('resume')" class="button dark" type="button" :disabled="pending[run.id]" @click="control('resume')"><Play :size="14" />继续</button>
        <button v-if="run?.actions.includes('continue')" class="button dark" type="button" :disabled="pending[run.id]" @click="control('continue')"><CheckCheck :size="14" />处理后继续</button>
        <button v-if="run?.actions.includes('stop')" class="button light" type="button" :disabled="pending[run.id]" @click="control('stop')"><Square :size="14" />停止</button>
        <button v-if="run?.actions.includes('retry')" class="button light" type="button" :disabled="pending[run.id]" @click="control('retry')"><RotateCcw :size="14" />重试</button>
        <button v-if="run && !active.has(run.status)" class="button light" type="button" @click="emit('result',run.id)"><Image :size="14" />查看结果</button>
        <LoaderCircle v-if="run && pending[run.id]" class="spinning" :size="16" />
      </div>
      <div v-if="run && active.has(run.status)" class="learning-progress"><div><strong>{{ run.current_chapter || '准备课程' }}</strong><span>{{ run.progress }}%</span></div><div class="progress-track"><span :style="{ transform: `scaleX(${run.progress / 100})` }"></span></div><p v-if="run.current_unit">{{ run.current_unit }} <span>{{ clock(run.position) }} / {{ clock(run.duration) }}</span></p><p v-if="run.error" class="learning-blocker">{{ conciseMessage(run.error) }}</p></div>
      <p v-else-if="run?.error" class="learning-result-line" :class="run.status">{{ conciseMessage(run.error) }}</p>
      <div class="learning-live-grid">
        <section class="learning-browser"><LiveBrowser v-if="run?.browser_available" ref="liveViewer" :key="run.id" :run-id="run.id" :native-browser="!run.headless" @open-browser="control('open_browser')" /><ScreenshotViewer v-else-if="image" :image="image" :segments="sections" alt-prefix="网课结果截图" /><div v-else class="empty-state"><MonitorOff v-if="run && !active.has(run.status)" :size="28" /><BookOpen v-else :size="28" /><h3>{{ run && !active.has(run.status) ? '本次没有保存截图' : run && active.has(run.status) ? '正在连接课程浏览器' : '课程浏览器' }}</h3></div></section>
        <aside class="learning-log"><h3>执行日志 <span class="count">{{ events.length }}</span></h3><div class="learning-log-stream" role="log" aria-live="polite"><article v-for="event in [...events].reverse().slice(0,100)" :key="event.id"><span class="event-dot" :class="event.kind"></span><div><strong>{{ logMessage(event) }}</strong><time>{{ new Date(event.created_at).toLocaleTimeString('zh-CN') }}</time></div></article><p v-if="!events.length" class="empty-inline">暂无执行日志</p></div></aside>
      </div>
    </section>
    <Transition name="dialog"><div v-if="addCourseOpen" class="modal-backdrop" @mousedown.self="addCourseOpen=false"><section class="modal-card" role="dialog" aria-modal="true" aria-labelledby="add-course-title">
      <div class="modal-heading"><h2 id="add-course-title">加入{{ platformById[account?.platform]?.label }}课程</h2><button class="modal-close" type="button" aria-label="关闭加入课程" @click="addCourseOpen=false"><X :size="16" /></button></div>
      <label class="field-label"><span>课程名称</span><input v-model="courseForm.name" aria-label="课程名称" maxlength="200"></label>
      <label class="field-label"><span>课程学习链接</span><input v-model="courseForm.url" type="url" aria-label="课程学习链接" placeholder="https://"></label>
      <p v-if="addError" role="alert" class="learning-error">{{ addError }}</p>
      <div class="modal-actions"><button class="button light" type="button" @click="addCourseOpen=false">取消</button><button class="button dark" type="button" :disabled="addingCourse || !courseForm.name.trim() || !courseForm.url.trim()" @click="addCourse"><LoaderCircle v-if="addingCourse" class="spinning" :size="14" />加入</button></div>
    </section></div></Transition>
  </main>
</template>

<style scoped>
.learning-browser-mode { margin-left: auto; color: var(--muted); font-size: 11px; white-space: nowrap; }
.learning-workspace { grid-column: 2; grid-row: 2; display: grid; grid-template-columns: 320px minmax(0,1fr); min-height: 0; min-width: 0; background: var(--surface); }
.learning-courses { display: flex; flex-direction: column; gap: 16px; padding: 22px 16px; min-height: 0; border-right: 1px solid var(--line); }
.learning-title { display: flex; align-items: center; gap: 9px; }
.learning-title h2 { font-size: 19px; }
.learning-filters { display: flex; flex-wrap: wrap; align-items: center; gap: 7px; }
.learning-filters select { min-width: 110px; max-width: 150px; }
.learning-filters input { width: 75px; }
.learning-filters select,.learning-filters input { height: 32px; border: 1px solid var(--line); background: var(--surface); border-radius: 4px; padding: 0 6px; font-size: 12px; }
.learning-filters .button { font-size: 11px; min-height: 32px; padding: 5px 9px; }
.learning-search { display: flex; align-items: center; gap: 8px; border: 1px solid var(--line); border-radius: 5px; padding: 8px; color: var(--muted); }
.learning-search input { min-width: 0; flex: 1; border: 0; background: transparent; font-size: 12px; outline: none; }
.learning-course-list { flex: 1; min-height: 0; overflow-y: auto; }
.learning-course-list article { display: flex; align-items: center; gap: 8px; border-bottom: 1px solid var(--line); padding: 13px 5px; }
.learning-course-list article.selected { background: var(--accent-soft); }
.learning-course-select { display: flex; flex: 1; align-items: center; gap: 9px; min-width: 0; border: 0; background: transparent; padding: 4px; text-align: left; }
.learning-course-select span { min-width: 0; }
.learning-course-select strong { display: block; font-size: 12px; line-height: 1.7; overflow-wrap: anywhere; }
.learning-course-select small { display: block; font-size: 10px; color: var(--muted); margin-top: 5px; }
.learning-detail { display: flex; flex-direction: column; gap: 15px; padding: 22px; min-width: 0; min-height: 0; }
.learning-run-heading { display: flex; align-items: start; justify-content: space-between; gap: 12px; }
.learning-run-heading > div { min-width: 0; }
.learning-run-heading h2 { font-size: 17px; line-height: 1.6; margin-top: 10px; overflow-wrap: anywhere; }
.learning-retry-count { margin-left: 8px; color: var(--orange); font-size: 10px; white-space: nowrap; }
.learning-controls { display: flex; align-items: center; flex-wrap: wrap; gap: 7px; min-height: 32px; }
.learning-controls .button { font-size: 11px; min-height: 32px; }
.learning-progress > div:first-child { display: flex; justify-content: space-between; gap: 10px; font-size: 12px; }
.learning-progress .progress-track { margin-top: 10px; }
.learning-progress p { display: flex; justify-content: space-between; gap: 12px; font-size: 12px; line-height: 1.7; margin-top: 9px; overflow-wrap: anywhere; }
.learning-progress .learning-blocker { display: block; color: var(--orange); background: var(--orange-soft); padding: 10px; border-radius: 4px; }
.learning-result-line { font-size: 12px; line-height: 1.7; color: var(--muted); overflow-wrap: anywhere; }
.learning-result-line.interrupted,.learning-result-line.incomplete { color: var(--red); }
.learning-live-grid { display: grid; grid-template-columns: minmax(0,1fr) 260px; flex: 1; min-height: 0; gap: 18px; }
.learning-browser { min-height: 0; min-width: 0; overflow: hidden; border: 1px solid var(--line); border-radius: 5px; background: var(--bg); }
.learning-log { display: flex; flex-direction: column; gap: 12px; min-height: 0; min-width: 0; }
.learning-log h3 { display: flex; justify-content: space-between; gap: 6px; font-size: 13px; }
.learning-log-stream { overflow-y: auto; min-height: 0; flex: 1; }
.learning-log-stream article { display: flex; align-items: start; gap: 9px; border-bottom: 1px solid var(--line); padding: 12px 0; }
.learning-log-stream .event-dot { margin-top: 6px; flex: none; }
.learning-log-stream strong { font-size: 11px; line-height: 1.8; font-weight: 500; overflow-wrap: anywhere; }
.learning-log-stream time { display: block; color: var(--muted); font-size: 10px; margin-top: 5px; }
.learning-message { font-size: 11px; color: var(--green); }
.learning-error { display: flex; align-items: center; gap: 8px; padding: 10px; color: var(--red); background: var(--red-soft); font-size: 12px; overflow-wrap: anywhere; border-radius: 4px; }
.job-badge.awaiting_user { color: var(--orange); background: var(--orange-soft); }
@media(max-width:1400px) { .learning-workspace { grid-template-columns: 285px minmax(0,1fr); } .learning-detail { padding: 18px; } .learning-live-grid { grid-template-columns: minmax(0,1fr) 225px; gap: 12px; } }
</style>
