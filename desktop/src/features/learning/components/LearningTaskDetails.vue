<script setup>
import { computed, nextTick, onUnmounted, ref, watch } from "vue";
import { BookOpen, CheckCheck, CircleAlert, FileText, Image, LoaderCircle, RefreshCw, X } from "@lucide/vue";
import { api } from "../../../app/api.js";
import { loadLearningResult, learningStatusLabel } from "../lib/result.js";
import { conciseMessage } from "../../tasks/lib/presentation.js";
import PlatformLogo from "../../platforms/components/PlatformLogo.vue";
import ScreenshotViewer from "../../tasks/components/ScreenshotViewer.vue";
import Tabs from "../../../shared/controls/Tabs.vue";
const props = defineProps({ open: Boolean, runId: { type: String, default: "" } });
const emit = defineEmits(["close", "workspace"]);
const result = ref(null), loading = ref(false), error = ref(""), tab = ref("result"), dialog = ref(null);
const run = computed(() => result.value?.run), events = computed(() => result.value?.events || []);
const tabs = computed(() => [{ value: "result", label: "最终结果" }, { value: "logs", label: "执行日志", count: events.value.length }]);
const active = new Set(["queued", "running", "paused", "awaiting_user", "stopping"]);
const date = value => value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "—";
let generation = 0, timer = null, previousFocus = null;
async function load() {
  clearTimeout(timer);
  const token = ++generation, id = props.runId;
  if (!props.open || !id) return;
  loading.value = true; error.value = "";
  try {
    const response = await loadLearningResult(api, id);
    if (!props.open || token !== generation) return;
    result.value = response;
    if (active.has(response.run.status)) timer = setTimeout(load, 2000);
  } catch (failure) { if (token === generation) error.value = conciseMessage(failure.message); }
  finally { if (token === generation) loading.value = false; }
}
watch([() => props.open, () => props.runId], async ([open]) => {
  generation++; clearTimeout(timer);
  if (!open) { if (previousFocus?.isConnected) previousFocus.focus(); return; }
  previousFocus = document.activeElement; result.value = null; tab.value = "result";
  await nextTick(); dialog.value?.focus(); await load();
}, { immediate: true });
onUnmounted(() => { generation++; clearTimeout(timer); });
function trap(event) {
  if (event.key !== "Tab") return;
  const items = [...dialog.value.querySelectorAll("button:not(:disabled),a[href],select:not(:disabled),[tabindex='0']")];
  const first = items[0], last = items.at(-1);
  if (event.shiftKey && [first, dialog.value].includes(document.activeElement)) { event.preventDefault(); last?.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
}
</script>

<template>
  <Transition name="dialog"><div v-if="open" class="modal-backdrop learning-result-backdrop" @mousedown.self="emit('close')" @keydown.esc.stop="emit('close')">
    <section ref="dialog" class="modal-card task-details-modal learning-result-modal" role="dialog" aria-modal="true" aria-labelledby="learning-result-title" tabindex="-1" @keydown="trap">
      <header class="learning-result-heading"><div><span class="panel-label"><PlatformLogo :platform="run?.platform || 'chaoxing'" :size="19" />{{ run?.account_name || '网课任务' }}<span v-if="run">第 {{ run.attempt }} 次执行</span><span v-if="run?.auto_retry_count" class="learning-result-retry">自动重试 {{ run.auto_retry_count }}/3</span></span><h2 id="learning-result-title">{{ run?.course_name || '正在读取任务结果' }}</h2></div><span v-if="run" class="job-badge" :class="run.status">{{ learningStatusLabel(run.status) }}</span><button class="icon-button" type="button" aria-label="关闭网课任务详情" title="关闭" @click="emit('close')"><X :size="18" /></button></header>
      <div v-if="error" class="learning-result-error" role="alert"><CircleAlert :size="16" /><span>{{ error }}</span><button class="button light" type="button" @click="load"><RefreshCw :size="14" />重新加载</button></div>
      <div v-if="!result && loading" class="learning-result-empty" role="status"><LoaderCircle class="spinning" :size="22" />正在读取结果与截图</div>
      <template v-else-if="result">
        <div class="learning-result-summary" :class="{ verified: result.summary.verified }"><CheckCheck v-if="result.summary.verified" :size="19" /><CircleAlert v-else :size="19" /><div><strong>{{ result.summary.title }}</strong><span>{{ date(run.finished_at || run.updated_at) }}<template v-if="result.summary.total"> · 本次已核验 {{ result.summary.completed }} / {{ result.summary.total }} 个目标章节</template></span></div><span class="learning-evidence-count"><Image :size="14" />{{ result.sections.length }} 段截图</span></div>
        <Tabs v-model="tab" :items="tabs" label="网课任务详情视图" />
        <div v-if="tab === 'result'" class="learning-result-content"><section class="learning-result-captures" aria-label="网课结果截图"><ScreenshotViewer v-if="result.sections.length" :segments="result.sections" alt-prefix="网课结果截图" /><div v-else class="learning-result-empty"><Image :size="25" /><strong>本次没有保存截图</strong><span>执行记录可在“执行日志”中查看。</span></div></section><aside class="learning-verifications"><h3><CheckCheck :size="15" />核验记录</h3><article v-for="event in result.summary.checkpoints" :key="event.id"><span class="event-dot" :class="event.kind"></span><div><strong>{{ conciseMessage(event.message) }}</strong><time>{{ date(event.created_at) }}</time></div></article><p v-if="!result.summary.checkpoints.length" class="empty-inline">本次尚无章节核验记录</p></aside></div>
        <section v-else class="learning-result-logs" aria-label="网课执行日志"><article v-for="event in [...events].reverse()" :key="event.id"><span class="event-dot" :class="event.kind"></span><div><strong>{{ conciseMessage(event.message, { kind: event.kind }) }}</strong><time>{{ date(event.created_at) }}</time></div></article><p v-if="!events.length" class="empty-inline">本次没有执行日志</p></section>
        <footer class="learning-result-footer"><button class="button light" type="button" @click="emit('workspace',run)"><BookOpen :size="14" />网课工作区</button><button class="button dark" type="button" @click="emit('close')">完成</button></footer>
      </template>
    </section>
  </div></Transition>
</template>

<style scoped>
.learning-result-backdrop { z-index: 70; }
.learning-result-modal { outline: none; }
.learning-result-heading { display: flex; align-items: start; gap: 14px; }
.learning-result-heading > div { flex: 1; min-width: 0; }
.learning-result-heading .panel-label { display: flex; align-items: center; gap: 8px; }
.learning-result-heading .panel-label > span { margin-left: 5px; color: var(--muted); }
.learning-result-heading .learning-result-retry { color: var(--orange); }
.learning-result-heading h2 { font-size: 19px; line-height: 1.5; margin-top: 8px; overflow-wrap: anywhere; }
.learning-result-summary { display: flex; align-items: center; gap: 12px; border-block: 1px solid var(--line); padding: 14px 0; color: var(--orange); }
.learning-result-summary.verified { color: var(--green); }
.learning-result-summary > div { flex: 1; min-width: 0; }
.learning-result-summary strong { display: block; font-size: 13px; line-height: 1.6; }
.learning-result-summary > div > span { display: block; color: var(--muted); margin-top: 4px; font-size: 11px; }
.learning-evidence-count { display: flex; align-items: center; gap: 6px; color: var(--muted); white-space: nowrap; font-size: 12px; }
.learning-result-content { display: grid; grid-template-columns: minmax(0,1fr) 270px; gap: 20px; flex: 1; min-height: 0; }
.learning-result-captures { min-width: 0; min-height: 0; border: 1px solid var(--line); border-radius: 5px; overflow: hidden; }
.learning-verifications,.learning-result-logs { min-height: 0; overflow-y: auto; }
.learning-verifications h3 { display: flex; gap: 7px; align-items: center; font-size: 13px; padding-bottom: 8px; }
.learning-verifications article,.learning-result-logs article { display: flex; align-items: start; gap: 9px; padding: 12px 0; border-bottom: 1px solid var(--line); }
.learning-verifications .event-dot,.learning-result-logs .event-dot { margin-top: 6px; flex: none; }
.learning-verifications strong,.learning-result-logs strong { font-size: 12px; line-height: 1.8; font-weight: 500; overflow-wrap: anywhere; }
.learning-verifications time,.learning-result-logs time { display: block; color: var(--muted); font-size: 10px; margin-top: 5px; }
.learning-result-logs { flex: 1; }
.learning-result-empty { flex: 1; min-height: 160px; height: 100%; display: flex; align-items: center; justify-content: center; flex-direction: column; gap: 12px; color: var(--muted); font-size: 12px; }
.learning-result-empty strong { font-size: 14px; font-weight: 500; }
.learning-result-error { display: flex; align-items: center; gap: 10px; color: var(--red); padding: 12px; background: var(--red-soft); font-size: 12px; }
.learning-result-error > span { flex: 1; }
.learning-result-footer { display: flex; gap: 8px; padding-top: 12px; border-top: 1px solid var(--line); }
.learning-result-footer > :last-child { margin-left: auto; }
.learning-result-modal button:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
@media (max-width: 1150px) { .learning-result-content { grid-template-columns: minmax(0,1fr) 210px; gap: 14px; } }
</style>
