<script setup>
import { onBeforeUnmount, ref } from "vue";
import { GripVertical } from "@lucide/vue";
import { clamp, resistEdge } from "./motion";

const props = defineProps({
  modelValue: { type: Number, default: 370 },
  min: { type: Number, default: 300 },
  max: { type: Number, default: 560 },
  label: { type: String, default: "调整作业列表宽度" }
});
const emit = defineEmits(["update:modelValue", "commit"]);
const dragging = ref(false);
const pull = ref(0);
let drag = null;
function start(event) {
  if (event.button !== 0) return;
  event.preventDefault();
  drag = { id: event.pointerId, x: event.clientX, initial: props.modelValue, target: event.currentTarget };
  event.currentTarget.setPointerCapture(event.pointerId);
  dragging.value = true;
}
function move(event) {
  if (!drag || drag.id !== event.pointerId) return;
  const raw = drag.initial + event.clientX - drag.x;
  const legal = clamp(raw, props.min, props.max);
  pull.value = resistEdge(raw, props.min, props.max) - legal;
  emit("update:modelValue", Math.round(legal));
}
function finish(cancel = false) {
  if (!drag) return;
  const previous = drag;
  drag = null;
  if (cancel) emit("update:modelValue", previous.initial);
  if (previous.target.hasPointerCapture(previous.id)) previous.target.releasePointerCapture(previous.id);
  dragging.value = false;
  pull.value = 0;
  emit("commit");
}
function navigate(event) {
  const step = event.shiftKey ? 40 : 12;
  const values = { ArrowLeft: props.modelValue - step, ArrowRight: props.modelValue + step, Home: props.min, End: props.max };
  if (event.key === "Escape" && drag) { event.preventDefault(); finish(true); return; }
  if (!(event.key in values)) return;
  event.preventDefault();
  emit("update:modelValue", clamp(values[event.key], props.min, props.max));
  emit("commit");
}
onBeforeUnmount(() => finish(true));
</script>

<template>
  <div class="lei-resize-handle" :class="{ dragging }" tabindex="0" role="separator"
    aria-orientation="vertical" :aria-label="label" :aria-valuenow="modelValue"
    :aria-valuemin="min" :aria-valuemax="max" :aria-valuetext="`${modelValue} 像素`"
    title="拖动调整宽度，双击重置" @pointerdown="start" @pointermove="move"
    @pointerup="finish()" @pointercancel="finish(true)" @lostpointercapture="finish()"
    @keydown="navigate" @dblclick="emit('update:modelValue', 370); emit('commit')">
    <span :style="{ transform: `translateX(${pull}px)` }"><GripVertical :size="14" aria-hidden="true" /></span>
  </div>
</template>
