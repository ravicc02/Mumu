<script setup>
import { computed, nextTick, onUnmounted, ref, watch } from "vue";
import { BookOpen, Check, LoaderCircle, RefreshCw, X } from "@lucide/vue";
import { api } from "../../../app/api.js";
const props = defineProps({ account: { type: Object, required: true } });
const emit = defineEmits(["changed"]);
const records = ref([]), draft = ref([]), opened = ref(false), pending = ref(false), saving = ref(false), error = ref("");
const discovered = ref(false);
const trigger = ref(null), closeButton = ref(null);
let generation = 0;
const selected = computed(() => records.value.filter(item => item.selected && item.active && item.accessible));
async function load(refresh = false) {
  if (pending.value || props.account.status !== "ready") return;
  const id = props.account.id, token = generation;
  pending.value = true; error.value = "";
  try {
    const result = await (refresh ? api.collectPlatformCourses(id) : api.platformCourses(id));
    if (token !== generation) return;
    records.value = result;
    if (refresh) discovered.value = true;
    draft.value = result.filter(item => item.selected).map(item => item.id);
    if (refresh && !draft.value.length) opened.value = true;
  } catch (failure) { if (token === generation) error.value = failure.message; }
  finally { if (token === generation) pending.value = false; }
}
async function save() {
  if (saving.value) return;
  const id = props.account.id, token = generation;
  saving.value = true; error.value = "";
  try {
    const result = await api.selectPlatformCourses(id,draft.value);
    if (token !== generation) return;
    records.value = result; opened.value = false; emit("changed");
  } catch (failure) { if (token === generation) error.value = failure.message; }
  finally { if (token === generation) saving.value = false; }
}
function show() { draft.value = records.value.filter(item => item.selected && item.active && item.accessible).map(item => item.id); opened.value = true; }
watch([() => props.account.id,() => props.account.status], async () => { generation++; records.value = []; pending.value = false; saving.value = false; opened.value = false; discovered.value = false; await load(); }, { immediate: true });
watch(opened, async value => { await nextTick(); (value ? closeButton.value : trigger.value)?.focus(); });
onUnmounted(() => { generation++; });
</script>
<template>
  <div class="educoder-course-scope">
    <button ref="trigger" class="button light" type="button" :disabled="account.status !== 'ready' || pending" @click="show"><BookOpen :size="14" />{{ selected.length ? selected.length + ' 个课堂' : '选择课堂' }}</button>
    <span v-if="selected.length" class="educoder-course-names" :title="selected.map(item => item.name).join('、')">{{ selected.map(item => item.name).join('、') }}</span>
    <button class="icon-button" type="button" :disabled="pending || account.status !== 'ready'" title="获取正在进行的课堂" aria-label="获取正在进行的课堂" @click="load(true)"><LoaderCircle v-if="pending" :size="15" class="spinning" /><RefreshCw v-else :size="15" /></button>
    <p v-if="error && !opened" class="validation-error" role="alert">{{ error }}</p>
  </div>
  <Teleport to="body"><Transition name="dialog"><div v-if="opened" class="modal-backdrop" @mousedown.self="!saving && (opened = false)" @keydown.esc="!saving && (opened = false)">
    <section class="modal-card educoder-courses-dialog" role="dialog" aria-modal="true" aria-labelledby="educoder-course-title" tabindex="-1">
      <header class="modal-heading"><h2 id="educoder-course-title">选择课堂</h2><button ref="closeButton" class="modal-close" type="button" aria-label="关闭课程选择" :disabled="saving" @click="opened = false"><X :size="17" /></button></header>
      <div class="educoder-course-toolbar"><span>{{ account.name }}</span><button class="button light" type="button" :disabled="pending || saving" @click="load(true)"><RefreshCw :size="14" :class="{spinning:pending}" />获取课堂</button></div>
      <div v-if="pending" class="empty-inline" role="status">正在获取课堂…</div>
      <div v-else-if="!records.length" class="empty-inline">{{ discovered ? '当前没有进行中的学习课堂' : '尚未获取课堂' }}</div>
      <div v-else class="educoder-course-list"><label v-for="course in records" :key="course.id" :class="{unavailable:!course.active || !course.accessible}"><input v-model="draft" type="checkbox" :value="course.id" :disabled="saving || !course.active || !course.accessible"><strong>{{ course.name }}</strong><span v-if="!course.active">已结束</span><span v-else-if="!course.accessible">无权限</span><Check v-else-if="draft.includes(course.id)" :size="14" /></label></div>
      <p v-if="error" class="validation-error" role="alert">{{ error }}</p>
      <footer class="modal-actions"><span class="educoder-course-count">已选 {{ draft.length }}</span><button class="button light" type="button" :disabled="saving" @click="opened = false">取消</button><button class="button dark" type="button" :disabled="saving || pending" @click="save"><LoaderCircle v-if="saving" :size="14" class="spinning" />保存选择</button></footer>
    </section>
  </div></Transition></Teleport>
</template>
<style scoped>
.educoder-course-scope { display:flex; align-items:center; gap:8px; flex-wrap:wrap; padding:0 0 14px; }
.educoder-course-names { flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:12px; color:var(--muted); }
.educoder-course-scope p { flex-basis:100%; font-size:12px; }
.educoder-courses-dialog { width:540px; max-width:calc(100vw - 64px); }
.educoder-course-toolbar { display:flex; align-items:center; justify-content:space-between; margin:12px 0; color:var(--muted); font-size:12px; }
.educoder-course-list { max-height:380px; overflow:auto; border-top:1px solid var(--line); }
.educoder-course-list label { display:flex; align-items:center; gap:12px; padding:16px 4px; border-bottom:1px solid var(--line); cursor:pointer; }
.educoder-course-list strong { flex:1; min-width:0; overflow-wrap:anywhere; font-size:13px; }
.educoder-course-list svg { color:var(--accent); }
.educoder-course-list span,.educoder-course-count { color:var(--muted); font-size:11px; }
.educoder-course-count { margin-right:auto; }.educoder-course-list .unavailable { color:var(--muted); }
</style>
