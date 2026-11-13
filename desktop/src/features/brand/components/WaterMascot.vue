<script setup>
import { onMounted, onUnmounted, ref, watch } from "vue";
import { createWaterMascot } from "../lib/mascot.js";

const props = defineProps({ mood: { type: String, default: "idle" }, water: { type: Number, default: .09 }, total: { type: Number, default: 0 }, paused: Boolean, detail: Boolean });
const canvas = ref(null);
const failed = ref(false);
let renderer;
const sync = () => renderer?.setState({ mood: props.mood, water: props.water, total: props.total, paused: props.paused });
function look(event) {
  const box = canvas.value.getBoundingClientRect();
  renderer?.look((event.clientX - box.left) / box.width * 2 - 1, (event.clientY - box.top) / box.height * 2 - 1);
}
watch(() => [props.mood, props.water, props.total, props.paused], sync);
onMounted(() => { try { renderer = createWaterMascot(canvas.value, { detail: props.detail }); sync(); } catch { failed.value = true; } });
onUnmounted(() => renderer?.dispose());
</script>

<template>
  <span class="water-mascot" :data-mood="mood" :data-water="water" @pointermove="look" @pointerleave="renderer?.look(0, 0)" @pointerdown="renderer?.poke()">
    <canvas ref="canvas" aria-hidden="true" />
    <span v-if="failed" class="mascot-render-error">角色画面不可用</span>
  </span>
</template>
