<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref } from "vue";
import { Ellipsis, FileText, Trash2 } from "@lucide/vue";

const props = defineProps({ task: { type: Object, required: true } });
const emit = defineEmits(["details", "delete"]);
const trigger = ref(null), menu = ref(null), opened = ref(false);
const position = ref({ top: "0px", left: "0px" });
const deletable = computed(() => props.task.actions?.includes("delete"));
const detailsLabel = computed(() => props.task.task_kind === "learning" && ["completed", "incomplete", "interrupted", "stopped"].includes(props.task.status) ? "查看结果" : "查看任务");

async function toggle() {
  opened.value = !opened.value;
  if (!opened.value) return;
  await nextTick();
  const anchor = trigger.value.getBoundingClientRect();
  const bounds = menu.value.getBoundingClientRect();
  position.value = {
    left: Math.max(8, Math.min(anchor.right - bounds.width, window.innerWidth - bounds.width - 8)) + "px",
    top: Math.max(8, anchor.bottom + bounds.height + 8 > window.innerHeight ? anchor.top - bounds.height - 4 : anchor.bottom + 4) + "px"
  };
  menu.value.querySelector("button:not(:disabled)")?.focus();
}
function choose(action) { opened.value = false; emit(action, props.task); }
function outside(event) {
  if (!menu.value?.contains(event.target) && !trigger.value?.contains(event.target)) opened.value = false;
}
function keys(event) {
  if (event.key === "Escape") { event.stopPropagation(); opened.value = false; trigger.value?.focus(); }
  if (event.key === "Tab") { opened.value = false; return; }
  if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  const items = [...menu.value.querySelectorAll("button:not(:disabled)")];
  const index = items.indexOf(document.activeElement);
  items[event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : (index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length]?.focus();
}
function close() { opened.value = false; }
onMounted(() => { document.addEventListener("pointerdown", outside); window.addEventListener("resize", close); document.addEventListener("scroll", close, true); });
onUnmounted(() => { document.removeEventListener("pointerdown", outside); window.removeEventListener("resize", close); document.removeEventListener("scroll", close, true); });
</script>

<template>
  <button ref="trigger" :id="`task-actions-${task.id}`" class="job-more-button" type="button" :aria-label="`${task.assignment_title}（${task.account_name || '未知账号'}）任务操作`" title="任务操作" aria-haspopup="menu" :aria-expanded="opened" @click="toggle" @keydown.esc="close"><Ellipsis :size="18" /></button>
  <Teleport to="body">
    <Transition name="task-menu">
      <div v-if="opened" ref="menu" class="account-menu task-actions-menu" :style="position" role="menu" aria-label="任务操作" @keydown="keys">
        <button type="button" role="menuitem" @click="choose('details')"><FileText :size="14" />{{ detailsLabel }}</button>
        <button class="danger" type="button" role="menuitem" :disabled="!deletable" :title="deletable ? '删除任务' : '请先停止任务并关闭任务浏览器'" @click="choose('delete')"><Trash2 :size="14" />删除任务</button>
      </div>
    </Transition>
  </Teleport>
</template>
