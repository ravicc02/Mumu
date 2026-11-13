<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import { Check, ChevronDown, SlidersHorizontal } from "@lucide/vue";
import { pillFlightFrames } from "./motion";

const props = defineProps({ modelValue: { type: String, default: "all" }, items: { type: Array, required: true } });
const emit = defineEmits(["update:modelValue"]);
const root = ref(null);
const trigger = ref(null);
const socket = ref(null);
const open = ref(false);
const flying = ref(false);
const selected = computed(() => props.items.find(item => item.value === props.modelValue) || props.items[0]);
const menuId = `status-options-${Math.random().toString(36).slice(2, 9)}`;
let ghost = null;
let flight = null;
let timer = null;
let media;

function cancelFlight() {
  clearTimeout(timer);
  flight?.cancel();
  flight = null;
  ghost?.remove();
  ghost = null;
  flying.value = false;
}
function close(focus = false) {
  cancelFlight();
  open.value = false;
  if (focus) trigger.value?.focus();
}
async function toggle() {
  if (open.value) return close();
  open.value = true;
  await nextTick();
  const options = root.value.querySelectorAll('[role="option"]');
  const index = Math.max(0, props.items.findIndex(item => item.value === props.modelValue));
  options[index]?.focus();
}
async function choose(item, event) {
  cancelFlight();
  const pocket = event.currentTarget.querySelector('.status-capsule');
  const from = pocket.getBoundingClientRect();
  const to = socket.value.getBoundingClientRect();
  emit("update:modelValue", item.value);
  if (media?.matches || !pocket.animate) return close(true);
  const element = document.createElement("span");
  element.className = `status-capsule status-flight ${item.value}`;
  element.setAttribute("aria-hidden", "true");
  element.style.width = `${from.width}px`;
  element.style.height = `${from.height}px`;
  document.body.appendChild(element);
  ghost = element;
  flying.value = true;
  pocket.animate([
    { transform: 'scale(1)' }, { transform: 'scale(.72)', opacity: .25, offset: .5 }, { transform: 'scale(1)' }
  ], { duration: 340, easing: 'cubic-bezier(.22,1,.36,1)' });
  const currentFlight = flight = element.animate(pillFlightFrames(from, to), { duration: 480, fill: "forwards" });
  await currentFlight.finished.catch(() => {});
  if (flight !== currentFlight) return;
  ghost?.remove(); ghost = null; flight = null; flying.value = false;
  socket.value?.animate([
    { transform: 'translateY(-3px) scale(1.24)' },
    { transform: 'translateY(1px) scale(.92)', offset: .55 }, { transform: 'none' }
  ], { duration: 340, easing: 'cubic-bezier(.22,1,.36,1)' });
  timer = setTimeout(() => close(true), 100);
}
function navigate(event, index) {
  const options = [...root.value.querySelectorAll('[role="option"]')];
  let next = index;
  if (event.key === "ArrowDown" || event.key === "ArrowRight") next = (index + 1) % options.length;
  else if (event.key === "ArrowUp" || event.key === "ArrowLeft") next = (index + options.length - 1) % options.length;
  else if (event.key === "Home") next = 0;
  else if (event.key === "End") next = options.length - 1;
  else if (event.key === "Escape") { event.preventDefault(); close(true); return; }
  else return;
  event.preventDefault(); options[next]?.focus();
}
function outside(event) { if (!event.composedPath().includes(root.value)) close(); }
function onMotionChange() { if (media.matches) cancelFlight(); }
onMounted(() => {
  media = matchMedia("(prefers-reduced-motion: reduce)");
  media.addEventListener("change", onMotionChange);
  document.addEventListener("pointerdown", outside);
});
onBeforeUnmount(() => {
  cancelFlight();
  document.removeEventListener("pointerdown", outside);
  media?.removeEventListener("change", onMotionChange);
});
</script>

<template>
  <div ref="root" class="lei-status-select" :class="{ open }">
    <button ref="trigger" type="button" class="status-filter-trigger" aria-label="筛选任务状态"
      aria-haspopup="listbox" :aria-expanded="open" :aria-controls="menuId" @click="toggle"
      @keydown.down.prevent="!open && toggle()" @keydown.esc.prevent="close(true)">
      <SlidersHorizontal :size="14" /><span ref="socket" class="status-filter-socket"><span class="status-capsule" :class="[selected.value, { flying }]" aria-hidden="true"></span></span>
      <strong>{{ selected.label }}</strong><span class="status-filter-count">{{ selected.count }}</span><ChevronDown :size="13" class="status-filter-chevron" />
    </button>
    <Transition name="status-tray">
      <div v-if="open" :id="menuId" class="status-filter-tray" role="listbox" aria-label="任务状态">
        <button v-for="(item, index) in items" :key="item.value" type="button" role="option"
          :aria-selected="item.value === modelValue" :style="{ '--option-index': index }"
          @click="choose(item, $event)" @keydown="navigate($event, index)">
          <span class="status-option-pocket"><span class="status-capsule" :class="item.value"></span></span>
          <span>{{ item.label }}</span><span class="status-option-count">{{ item.count }}</span><Check v-if="item.value === modelValue" :size="12" />
        </button>
      </div>
    </Transition>
  </div>
</template>
