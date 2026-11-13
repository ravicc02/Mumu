<script setup>
import { onMounted, onUnmounted, ref } from 'vue';
const canvas = ref(null), failed = ref(false), ready = ref(false);
let scene, disposed = false;
onMounted(async () => {
  try { const { createBrandScene } = await import('../lib/scene.js'); if (!disposed) { scene = createBrandScene(canvas.value); ready.value = true; } }
  catch { failed.value = true; }
});
onUnmounted(() => { disposed = true; scene?.dispose(); });
</script>
<template><div class="brand-orbit" role="img" aria-label="Mumu 与六位不同形状的任务伙伴"><canvas v-if="!failed" ref="canvas" :data-ready="ready || undefined" /><img v-else src="/brand/mumu.png" alt="Mumu" /></div></template>
<style scoped>
/* finesse · component: mumu-brand-orbit · register=product · palette=glass+task-colors · motion=orbit */
.brand-orbit{width:100%;aspect-ratio:1.75;min-height:180px;position:relative}.brand-orbit canvas{display:block;width:100%;height:100%}.brand-orbit img{position:absolute;inset:0;width:100%;height:100%;object-fit:contain}
</style>
