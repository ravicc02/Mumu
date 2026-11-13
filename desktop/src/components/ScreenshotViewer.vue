<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { ChevronLeft, ChevronRight, ExternalLink, LoaderCircle, RefreshCw, ScanLine, ZoomIn, ZoomOut } from "@lucide/vue";
import { splitScreenshot } from "../lib/screenshots";

const props = defineProps({ image: { type: String, default: "" }, segments: { type: Array, default: () => [] }, altPrefix: { type: String, default: "作业截图" } });
const index = ref(0);
const zoom = ref(100);
const viewport = ref(null);
const automaticZoom = ref(true);
let observer;
const dimensions = ref({});
const failed = ref(false);
const reload = ref(0);
const sources = computed(() => props.segments.length ? props.segments : props.image ? [{ id: "current", src: props.image }] : []);
const pages = computed(() => sources.value.flatMap(source => {
  const size = dimensions.value[source.id] || source;
  const width = size.width || 1440, height = size.height || 900;
  const regions = source.index && source.count ? [{ y: 0, width, height }] : splitScreenshot(width, height, Math.round(width * 900 / 1440));
  return regions.map((region, part) => ({ ...region, source, sourceHeight: size.height || 900, key: `${source.id}:${part}` }));
}));
const page = computed(() => pages.value[Math.min(index.value, Math.max(0, pages.value.length - 1))]);
const loaded = computed(() => Boolean(page.value && dimensions.value[page.value.source.id]));
const sourceUrl = computed(() => {
  const src = page.value?.source.src || "";
  if (!reload.value || !src.startsWith("http")) return src;
  const url = new URL(src);
  url.searchParams.set("reload", reload.value);
  return url.href;
});
watch([
  () => props.segments.length ? "" : props.image,
  () => JSON.stringify(props.segments.map(section => [section.id, section.src])),
], () => {
  index.value = 0;
  dimensions.value = {};
  failed.value = false;
  reload.value = 0;
});
watch(index, () => { failed.value = false; });
function onLoad(event) {
  const image = event.target;
  dimensions.value[page.value.source.id] = { width: image.naturalWidth, height: image.naturalHeight };
  failed.value = false;
  sizeForReading();
}
function sizeForReading() {
  if (!automaticZoom.value || !viewport.value?.clientWidth || !page.value) return;
  const size = dimensions.value[page.value.source.id];
  if (size) zoom.value = 100;
}
function setZoom(value) { automaticZoom.value = false; zoom.value = value; }
onMounted(() => {
  if (typeof ResizeObserver !== "undefined") { observer = new ResizeObserver(sizeForReading); observer.observe(viewport.value); }
});
onBeforeUnmount(() => observer?.disconnect());
function move(direction) {
  index.value = Math.max(0, Math.min(pages.value.length - 1, index.value + direction));
}
function retry() { failed.value = false; reload.value += 1; }
</script>

<template>
  <div class="screenshot-viewer" @keydown.left.prevent="move(-1)" @keydown.right.prevent="move(1)">
    <div class="screenshot-toolbar">
      <button class="icon-button" type="button" title="上一段" aria-label="上一段截图" :disabled="index === 0" @click="move(-1)"><ChevronLeft :size="15" /></button>
      <select aria-label="截图分段" :value="index" @change="index = Number($event.target.value)">
        <option v-for="(_, part) in pages" :key="part" :value="part">第 {{ part + 1 }} / {{ pages.length }} 段</option>
      </select>
      <button class="icon-button" type="button" title="下一段" aria-label="下一段截图" :disabled="index >= pages.length - 1" @click="move(1)"><ChevronRight :size="15" /></button>
      <span class="screenshot-toolbar-spacer"></span>
      <button class="icon-button" type="button" title="缩小" aria-label="缩小截图" :disabled="zoom <= 100" @click="setZoom(zoom - 25)"><ZoomOut :size="15" /></button>
      <button class="icon-button" type="button" title="适应宽度" aria-label="截图适应宽度" @click="setZoom(100)"><ScanLine :size="15" /></button>
      <output class="screenshot-zoom" aria-label="截图缩放比例">{{ zoom }}%</output>
      <button class="icon-button" type="button" title="放大" aria-label="放大截图" :disabled="zoom >= 300" @click="setZoom(zoom + 25)"><ZoomIn :size="15" /></button>
      <a v-if="page" class="icon-button" :href="page.source.src" target="_blank" rel="noreferrer" title="查看原图" aria-label="查看当前截图原图"><ExternalLink :size="14" /></a>
    </div>
    <div ref="viewport" class="screenshot-viewport" tabindex="0" role="region" aria-label="分段截图">
      <div v-if="page" class="screenshot-page" :style="{ width: `${zoom}%`, aspectRatio: `${page.width} / ${page.height}` }">
        <img :key="`${page.key}:${reload}`" :src="sourceUrl" :alt="`${altPrefix}第 ${index + 1} 段`" :style="{ transform: `translateY(-${page.y / page.sourceHeight * 100}%)` }" @load="onLoad" @error="failed = true">
        <div v-if="failed" class="screenshot-feedback" role="alert"><span>截图加载失败</span><button class="button light" type="button" @click="retry"><RefreshCw :size="14" />重新加载</button></div>
        <div v-else-if="!loaded" class="screenshot-feedback" role="status"><LoaderCircle class="spinning" :size="20" /></div>
      </div>
      <div v-else class="screenshot-feedback">暂无截图</div>
    </div>
  </div>
</template>

<style scoped>
.screenshot-viewer { display: flex; flex-direction: column; width: 100%; min-width: 0; min-height: 0; height: 100%; background: var(--surface); }
.screenshot-toolbar { display: flex; align-items: center; gap: 2px; flex: none; padding: 5px 6px; border-bottom: 1px solid var(--line); }
.screenshot-toolbar .icon-button { width: 26px; min-width: 26px; height: 27px; }
.screenshot-toolbar-spacer { flex: 1; }
.screenshot-zoom { min-width: 34px; color: var(--muted); font-size: 10px; font-variant-numeric: tabular-nums; text-align: center; }
.screenshot-toolbar select { max-width: 120px; min-width: 84px; height: 27px; border: 0; border-radius: 4px; background: var(--surface); color: var(--muted); font-size: 11px; padding: 0 3px; }
.screenshot-toolbar :focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.screenshot-toolbar button:disabled { opacity: .4; cursor: not-allowed; }
.screenshot-viewport { flex: 1; min-height: 0; overflow: auto; overscroll-behavior: contain; }
.screenshot-page { position: relative; overflow: hidden; min-height: 0; }
.screenshot-page > img { position: absolute; top: 0; left: 0; display: block; width: 100%; height: auto; max-height: none; min-height: 0; border: 0; border-radius: 0; object-fit: initial; }
.screenshot-feedback { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; gap: 10px; color: var(--muted); background: var(--surface); font-size: 12px; }
@media (hover: hover) { .screenshot-toolbar select:hover { background: var(--surface-muted); } }
.screenshot-toolbar button:active:not(:disabled) { transform: translateY(1px); }
</style>
