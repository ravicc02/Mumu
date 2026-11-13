<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { stepSpring } from "./motion";

const props = defineProps({
  modelValue: { type: String, default: "all" },
  items: { type: Array, required: true },
  label: { type: String, default: "视图" }
});
const emit = defineEmits(["update:modelValue"]);
const root = ref(null);
const indicator = ref(null);
let observer;
let media;
let frame = 0;
let last = 0;
let ready = false;
const x = { p: 0, v: 0, target: 0 };
const width = { p: 0, v: 0, target: 0 };

function draw() {
  if (!indicator.value) return;
  indicator.value.style.transform = `translateX(${x.p}px)`;
  indicator.value.style.width = `${Math.max(0, width.p)}px`;
}
function tick(now) {
  frame = 0;
  const dt = Math.min(0.032, (now - (last || now - 16)) / 1000);
  last = now;
  let moving = false;
  for (let i = 0; i < 3; i += 1) {
    moving = stepSpring(x, dt / 3) || moving;
    moving = stepSpring(width, dt / 3) || moving;
  }
  draw();
  if (moving) frame = requestAnimationFrame(tick);
}
async function measure() {
  await nextTick();
  const selected = root.value?.querySelector('[aria-selected="true"]');
  if (!selected) return;
  x.target = selected.offsetLeft;
  width.target = selected.offsetWidth;
  cancelAnimationFrame(frame);
  frame = 0;
  if (!ready || media?.matches) {
    x.p = x.target;
    width.p = width.target;
    x.v = width.v = 0;
    draw();
  } else {
    last = 0;
    frame = requestAnimationFrame(tick);
  }
  ready = true;
}
function navigate(event, index) {
  let next = index;
  if (event.key === "ArrowRight") next = (index + 1) % props.items.length;
  else if (event.key === "ArrowLeft") next = (index + props.items.length - 1) % props.items.length;
  else if (event.key === "Home") next = 0;
  else if (event.key === "End") next = props.items.length - 1;
  else return;
  event.preventDefault();
  emit("update:modelValue", props.items[next].value);
  root.value.querySelectorAll('[role="tab"]')[next]?.focus();
}
watch(() => [props.modelValue, props.items], measure, { deep: true });
onMounted(() => {
  media = matchMedia("(prefers-reduced-motion: reduce)");
  media.addEventListener("change", measure);
  observer = new ResizeObserver(measure);
  observer.observe(root.value);
  measure();
});
onBeforeUnmount(() => {
  cancelAnimationFrame(frame);
  observer?.disconnect();
  media?.removeEventListener("change", measure);
});
</script>

<template>
  <div ref="root" class="lei-tabs" role="tablist" :aria-label="label">
    <span ref="indicator" class="lei-tabs-indicator" aria-hidden="true"></span>
    <button v-for="(item, index) in items" :key="item.value" type="button" role="tab"
      :aria-selected="modelValue === item.value" :tabindex="modelValue === item.value ? 0 : -1"
      :class="{ selected: modelValue === item.value }" @click="emit('update:modelValue', item.value)"
      @keydown="navigate($event, index)">
      <component :is="item.icon" v-if="item.icon" :size="14" aria-hidden="true" />
      {{ item.label }}<span v-if="item.count !== undefined" class="lei-tab-count">{{ item.count }}</span>
    </button>
  </div>
</template>
