<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue';
import {
  Check,
  CircleAlert,
  Clock3,
  GraduationCap,
  LoaderCircle,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Save,
  Trash2,
  Users,
  X,
} from '@lucide/vue';
import { api } from '../../../app/api.js';
import PlatformLogo from '../../platforms/components/PlatformLogo.vue';
import { accountsForBusiness } from '../../platforms/lib/catalog.js';

const props = defineProps({ accounts: { type: Array, default: () => [] } });
const weekdays = [
  { value: 1, label: '一' },
  { value: 2, label: '二' },
  { value: 3, label: '三' },
  { value: 4, label: '四' },
  { value: 5, label: '五' },
  { value: 6, label: '六' },
  { value: 7, label: '日' },
];
const groups = ref([]);
const loading = ref(true);
const error = ref('');
const notice = ref('');
const editorOpen = ref(false);
const editingId = ref('');
const saving = ref(false);
const courseBusy = ref(false);
const courseQuery = ref('');
const courseCatalog = ref([]);
const busy = ref({});
let refreshTimer = null;
const supportedAccounts = computed(() => accountsForBusiness(props.accounts, 'assignments'));
const accountsById = computed(() => new Map(props.accounts.map(account => [account.id, account])));
const defaultDraft = () => ({
  name: '',
  account_ids: [],
  course_filters: [],
  window: { days: [1, 2, 3, 4, 5, 6, 7], start: '00:00', end: '23:59' },
  auto_submit: true,
  enabled: true,
});
const draft = reactive(defaultDraft());
const filteredCourses = computed(() => {
  const query = courseQuery.value.trim().toLocaleLowerCase();
  if (!query) return courseCatalog.value;
  return courseCatalog.value.filter(course => course.name.toLocaleLowerCase().includes(query));
});

async function load({ silent = false } = {}) {
  if (!silent) loading.value = true;
  error.value = '';
  try {
    groups.value = await api.assignmentAutoGroups();
  } catch (cause) {
    error.value = cause.message;
  } finally {
    if (!silent) loading.value = false;
  }
}

function resetDraft() {
  Object.assign(draft, defaultDraft());
  courseCatalog.value = [];
  courseQuery.value = '';
}

function openCreate() {
  editingId.value = '';
  resetDraft();
  editorOpen.value = true;
  error.value = '';
  notice.value = '';
}

function openEdit(group) {
  editingId.value = group.id;
  Object.assign(draft, {
    name: group.name,
    account_ids: [...(group.account_ids || [])],
    course_filters: [...(group.course_filters || [])],
    window: { ...(group.windows?.[0] || defaultDraft().window), days: [...(group.windows?.[0]?.days || defaultDraft().window.days)] },
    auto_submit: Boolean(group.auto_submit),
    enabled: Boolean(group.enabled),
  });
  courseCatalog.value = (group.course_filters || []).map(name => ({
    name,
    account_ids: [...(group.account_ids || [])],
    account_count: group.account_ids?.length || 0,
    common: true,
  }));
  courseQuery.value = '';
  editorOpen.value = true;
  error.value = '';
  notice.value = '';
}

function closeEditor() {
  if (saving.value) return;
  editorOpen.value = false;
}

function toggleAccount(identifier) {
  draft.account_ids = draft.account_ids.includes(identifier)
    ? draft.account_ids.filter(item => item !== identifier)
    : [...draft.account_ids, identifier];
  courseCatalog.value = [];
  draft.course_filters = [];
}

function toggleDay(day) {
  draft.window.days = draft.window.days.includes(day)
    ? draft.window.days.filter(item => item !== day)
    : [...draft.window.days, day].sort((left, right) => left - right);
}

async function fetchCourses() {
  if (!draft.account_ids.length) {
    error.value = '请先选择账号';
    return;
  }
  courseBusy.value = true;
  error.value = '';
  notice.value = '';
  try {
    const result = await api.assignmentAutoGroupCourses(draft.account_ids);
    courseCatalog.value = result.courses || [];
    if (!courseCatalog.value.length) {
      notice.value = '所选账号暂无已采集课程，请先采集作业';
    }
  } catch (cause) {
    error.value = cause.message;
  } finally {
    courseBusy.value = false;
  }
}

function toggleCourse(name) {
  draft.course_filters = draft.course_filters.includes(name)
    ? draft.course_filters.filter(item => item !== name)
    : [...draft.course_filters, name];
}

function groupPayload() {
  return {
    name: draft.name.trim(),
    account_ids: [...new Set(draft.account_ids)],
    course_filters: [...new Set(draft.course_filters)],
    windows: [{
      days: [...new Set(draft.window.days)].sort((left, right) => left - right),
      start: draft.window.start,
      end: draft.window.end,
    }],
    auto_submit: draft.auto_submit,
    enabled: draft.enabled,
  };
}

async function saveGroup() {
  if (!draft.name.trim()) {
    error.value = '请填写分组名称';
    return;
  }
  if (!draft.account_ids.length) {
    error.value = '请选择至少一个账号';
    return;
  }
  if (!draft.window.days.length) {
    error.value = '请选择至少一个检测日';
    return;
  }
  saving.value = true;
  error.value = '';
  try {
    const payload = groupPayload();
    if (editingId.value) {
      await api.updateAssignmentAutoGroup(editingId.value, payload);
    } else {
      await api.createAssignmentAutoGroup(payload);
    }
    editorOpen.value = false;
    notice.value = editingId.value ? '自动处理分组已更新' : '自动处理分组已创建';
    await load();
  } catch (cause) {
    error.value = cause.message;
  } finally {
    saving.value = false;
  }
}

function replaceGroup(group) {
  groups.value = groups.value.map(item => item.id === group.id ? group : item);
}

async function toggleGroup(group) {
  if (busy.value[group.id]) return;
  busy.value[group.id] = 'toggle';
  error.value = '';
  try {
    replaceGroup(await api.updateAssignmentAutoGroup(group.id, { enabled: !group.enabled }));
  } catch (cause) {
    error.value = cause.message;
  } finally {
    delete busy.value[group.id];
  }
}

async function scanGroup(group) {
  if (busy.value[group.id]) return;
  busy.value[group.id] = 'scan';
  error.value = '';
  notice.value = `正在检测 ${group.name}`;
  try {
    replaceGroup(await api.scanAssignmentAutoGroup(group.id));
    notice.value = `${group.name} 检测完成`;
  } catch (cause) {
    error.value = cause.message;
  } finally {
    delete busy.value[group.id];
  }
}

async function deleteGroup(group) {
  if (busy.value[group.id]) return;
  if (!window.confirm(`删除自动处理分组“${group.name}”？`)) return;
  busy.value[group.id] = 'delete';
  error.value = '';
  try {
    await api.deleteAssignmentAutoGroup(group.id);
    groups.value = groups.value.filter(item => item.id !== group.id);
    notice.value = '自动处理分组已删除';
  } catch (cause) {
    error.value = cause.message;
  } finally {
    delete busy.value[group.id];
  }
}

function accountName(identifier) {
  return accountsById.value.get(identifier)?.name || '账号';
}

function groupAccountNames(group) {
  return (group.account_ids || []).map(accountName);
}

function windowLabel(group) {
  const window = group.windows?.[0];
  if (!window) return '全天';
  const days = window.days?.length === 7 ? '每天' : '周' + window.days.map(day => weekdays.find(item => item.value === day)?.label).join('、');
  return `${days} ${window.start}-${window.end}`;
}

function courseLabel(group) {
  const courses = group.course_filters || [];
  if (!courses.length) return '全部课程';
  return courses.length > 2 ? `${courses.slice(0, 2).join('、')} 等 ${courses.length} 门` : courses.join('、');
}

function statusLabel(group) {
  if (!group.enabled) return '已暂停';
  if (group.last_status === 'running') return '检测中';
  if (group.last_status === 'failed') return '检测失败';
  if (group.last_status === 'partial') return '部分完成';
  if (group.pending_count) return `${group.pending_count} 个待执行`;
  return group.last_scan_at ? '运行正常' : '等待检测';
}

function formatTime(value) {
  if (!value) return '';
  const date = new Date(value);
  return Number.isFinite(date.getTime())
    ? date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
    : '';
}

onMounted(() => {
  load();
  refreshTimer = window.setInterval(() => {
    if (!editorOpen.value && !document.hidden) load({ silent: true });
  }, 4000);
});
onUnmounted(() => {
  if (refreshTimer !== null) window.clearInterval(refreshTimer);
});
</script>

<template>
  <section class="assignment-automation">
    <header class="automation-heading">
      <div>
        <h3>自动处理</h3>
        <span>{{ groups.length }} 个分组</span>
      </div>
      <button class="button dark" type="button" @click="openCreate"><Plus :size="14" />新建分组</button>
    </header>

    <p v-if="error" class="automation-feedback error" role="alert"><CircleAlert :size="14" />{{ error }}</p>
    <p v-if="notice" class="automation-feedback" role="status"><Check :size="14" />{{ notice }}</p>

    <div v-if="loading" class="settings-loading"><LoaderCircle :size="18" class="spinning" />正在读取自动处理分组</div>
    <div v-else-if="!groups.length" class="empty-state automation-empty">
      <Clock3 :size="28" :stroke-width="1.4" />
      <h3>还没有自动处理分组</h3>
      <button class="button light" type="button" @click="openCreate"><Plus :size="14" />新建分组</button>
    </div>
    <div v-else class="automation-groups">
      <article v-for="group in groups" :key="group.id" class="automation-group" :class="{ disabled: !group.enabled }">
        <header class="automation-group-heading">
          <div>
            <strong>{{ group.name }}</strong>
            <span class="automation-status" :class="group.last_status">{{ statusLabel(group) }}</span>
          </div>
          <label class="automation-enable">
            <input class="switch-input" type="checkbox" :checked="group.enabled" :disabled="Boolean(busy[group.id])" :aria-label="group.name + ' 启用自动处理'" @change="toggleGroup(group)">
          </label>
        </header>
        <div class="automation-group-facts">
          <span><Clock3 :size="13" />{{ windowLabel(group) }}</span>
          <span><GraduationCap :size="13" />{{ courseLabel(group) }}</span>
          <span><Users :size="13" />{{ groupAccountNames(group).join('、') }}</span>
          <span><Save :size="13" />{{ group.auto_submit ? '自动提交' : '暂停待审' }}</span>
        </div>
        <p v-if="group.last_error" class="automation-group-error">{{ group.last_error }}</p>
        <footer class="automation-group-footer">
          <span>{{ group.last_scan_at ? '上次检测 ' + formatTime(group.last_scan_at) : '尚未检测' }}</span>
          <div>
            <button class="icon-button" type="button" :title="'立即检测：' + group.name" :aria-label="'立即检测：' + group.name" :disabled="Boolean(busy[group.id])" @click="scanGroup(group)">
              <LoaderCircle v-if="busy[group.id] === 'scan'" :size="14" class="spinning" />
              <RefreshCw v-else :size="14" />
            </button>
            <button class="icon-button" type="button" :title="'编辑：' + group.name" :aria-label="'编辑：' + group.name" :disabled="Boolean(busy[group.id])" @click="openEdit(group)"><Pencil :size="14" /></button>
            <button class="icon-button danger" type="button" :title="'删除：' + group.name" :aria-label="'删除：' + group.name" :disabled="Boolean(busy[group.id])" @click="deleteGroup(group)"><Trash2 :size="14" /></button>
          </div>
        </footer>
      </article>
    </div>

    <div v-if="editorOpen" class="modal-backdrop" @click.self="closeEditor">
      <section class="modal-card modal-wide assignment-automation-dialog" role="dialog" aria-modal="true" aria-labelledby="assignment-automation-title">
        <header class="modal-heading">
          <div>
            <h2 id="assignment-automation-title">{{ editingId ? '编辑自动处理分组' : '新建自动处理分组' }}</h2>
          </div>
          <button class="icon-button" type="button" aria-label="关闭自动处理分组" :disabled="saving" @click="closeEditor"><X :size="16" /></button>
        </header>
        <div class="automation-dialog-body">
          <label class="field-label">
            <span>分组名称</span>
            <input v-model="draft.name" maxlength="80" aria-label="分组名称" placeholder="例如：计算机科学课后作业">
          </label>
          <fieldset class="automation-fieldset">
            <legend>检测时间段</legend>
            <div class="automation-day-row">
              <button v-for="day in weekdays" :key="day.value" type="button" class="automation-day" :class="{ active: draft.window.days.includes(day.value) }" @click="toggleDay(day.value)">周{{ day.label }}</button>
            </div>
            <div class="automation-time-row">
              <label class="field-label"><span>开始</span><input v-model="draft.window.start" type="time" aria-label="检测开始时间"></label>
              <label class="field-label"><span>结束</span><input v-model="draft.window.end" type="time" aria-label="检测结束时间"></label>
            </div>
          </fieldset>
          <fieldset class="automation-fieldset">
            <legend>选择账号</legend>
            <div class="automation-account-grid">
              <label v-for="account in supportedAccounts" :key="account.id" class="automation-check">
                <input type="checkbox" :checked="draft.account_ids.includes(account.id)" @change="toggleAccount(account.id)">
                <PlatformLogo :platform="account.platform" :size="18" />
                <span>{{ account.name }}</span>
              </label>
            </div>
            <p v-if="!supportedAccounts.length" class="empty-inline">暂无支持作业的账号</p>
          </fieldset>
          <fieldset class="automation-fieldset">
            <div class="automation-field-heading">
              <legend>处理的课程</legend>
              <button class="button light" type="button" :disabled="courseBusy || !draft.account_ids.length" @click="fetchCourses"><LoaderCircle v-if="courseBusy" :size="13" class="spinning" /><RefreshCw v-else :size="13" />获取课程</button>
            </div>
            <label class="search-field automation-course-search"><input v-model="courseQuery" aria-label="搜索课程" placeholder="搜索课程"><span v-if="draft.course_filters.length">{{ draft.course_filters.length }} 已选</span></label>
            <div v-if="filteredCourses.length" class="automation-course-list">
              <label v-for="course in filteredCourses" :key="course.name" class="automation-check">
                <input type="checkbox" :checked="draft.course_filters.includes(course.name)" @change="toggleCourse(course.name)">
                <span>{{ course.name }}</span>
                <small>{{ course.common ? '所有账号' : course.account_count + ' 个账号' }}</small>
              </label>
            </div>
            <p v-else class="empty-inline">{{ courseCatalog.length ? '没有匹配课程' : '未选择课程时处理全部课程' }}</p>
          </fieldset>
          <div class="automation-options">
            <label class="automation-toggle"><input v-model="draft.auto_submit" class="switch-input" type="checkbox" role="switch"><span>自动提交</span></label>
            <label class="automation-toggle"><input v-model="draft.enabled" class="switch-input" type="checkbox" role="switch"><span>启用分组</span></label>
          </div>
        </div>
        <footer class="modal-actions">
          <button class="button light" type="button" :disabled="saving" @click="closeEditor">取消</button>
          <button class="button dark" type="button" :disabled="saving" @click="saveGroup"><LoaderCircle v-if="saving" :size="14" class="spinning" /><Play v-else :size="14" />{{ editingId ? '保存修改' : '创建分组' }}</button>
        </footer>
      </section>
    </div>
  </section>
</template>

<style scoped>
.assignment-automation { display: grid; gap: 16px; }
.automation-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; border-bottom: 1px solid var(--line); padding-bottom: 15px; }
.automation-heading > div { display: flex; align-items: baseline; gap: 9px; }
.automation-heading h3 { font-size: var(--text-md); }
.automation-heading span { color: var(--muted); font-size: var(--text-xs); }
.automation-feedback { display: flex; align-items: center; gap: 7px; color: var(--green); font-size: var(--text-xs); }
.automation-feedback.error { color: var(--red); }
.automation-groups { display: grid; gap: 10px; }
.automation-group { border: 1px solid var(--line); border-radius: 8px; background: var(--surface); overflow: hidden; }
.automation-group.disabled { opacity: .72; }
.automation-group-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 15px 16px 10px; }
.automation-group-heading > div { display: flex; align-items: center; min-width: 0; gap: 8px; }
.automation-group-heading strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: var(--text-sm); }
.automation-status { flex: none; border-radius: 3px; background: var(--surface-muted); padding: 2px 5px; color: var(--muted); font-size: var(--text-xs); }
.automation-status.running { background: var(--accent-soft); color: var(--accent); }
.automation-status.completed { background: var(--green-soft); color: var(--green); }
.automation-status.failed,.automation-status.partial { background: var(--red-soft); color: var(--red); }
.automation-enable { display: flex; }
.automation-group-facts { display: flex; flex-wrap: wrap; gap: 7px 14px; padding: 0 16px 13px; color: var(--muted); font-size: var(--text-xs); }
.automation-group-facts span { display: inline-flex; align-items: center; gap: 5px; min-width: 0; }
.automation-group-error { margin: 0 16px 12px; color: var(--red); font-size: var(--text-xs); overflow-wrap: anywhere; }
.automation-group-footer { display: flex; align-items: center; justify-content: space-between; gap: 10px; border-top: 1px solid var(--line); padding: 9px 12px 9px 16px; color: var(--subtle); font-size: var(--text-xs); }
.automation-group-footer > div { display: flex; gap: 3px; }
.automation-empty { min-height: 220px; }
.automation-dialog-body { display: grid; gap: 18px; }
.assignment-automation-dialog { display: grid; grid-template-rows: auto minmax(0,1fr) auto; max-height: calc(100dvh - 40px); overflow: hidden; }
.assignment-automation-dialog .modal-heading { margin-bottom: 14px; }
.assignment-automation-dialog .automation-dialog-body { min-height: 0; overflow-y: auto; padding-right: 4px; }
.assignment-automation-dialog .modal-actions { margin-top: 0; padding-top: 14px; }
.automation-fieldset { display: grid; gap: 10px; border: 0; border-top: 1px solid var(--line); padding: 15px 0 0; }
.automation-fieldset legend { padding: 0 7px 0 0; color: var(--ink); font-size: var(--text-sm); font-weight: 500; }
.automation-field-heading { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.automation-field-heading legend { padding-right: 0; }
.automation-day-row { display: flex; flex-wrap: wrap; gap: 6px; }
.automation-day { min-width: 42px; height: 30px; border: 1px solid var(--line); border-radius: 5px; background: var(--surface); color: var(--muted); font-size: var(--text-xs); }
.automation-day.active { border-color: var(--accent); background: var(--accent-soft); color: var(--accent); }
.automation-time-row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.automation-account-grid,.automation-course-list { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 7px 12px; }
.automation-check { display: flex; align-items: center; gap: 7px; min-width: 0; border: 1px solid var(--line); border-radius: 5px; padding: 8px 9px; color: var(--muted); font-size: var(--text-xs); }
.automation-check span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.automation-check small { margin-left: auto; color: var(--subtle); white-space: nowrap; }
.automation-course-search { display: flex; align-items: center; gap: 8px; }
.automation-course-search > span { color: var(--muted); font-size: var(--text-xs); white-space: nowrap; }
.automation-options { display: flex; align-items: center; gap: 24px; border-top: 1px solid var(--line); padding-top: 15px; }
.automation-toggle { display: flex; align-items: center; gap: 8px; color: var(--muted); font-size: var(--text-xs); }
@media (max-width: 760px) {
  .automation-account-grid,.automation-course-list { grid-template-columns: 1fr; }
  .automation-group-heading > div { flex-wrap: wrap; }
}
</style>
