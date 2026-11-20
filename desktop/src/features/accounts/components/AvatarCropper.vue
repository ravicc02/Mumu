<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from "vue";
import { Crop, LoaderCircle, X, ZoomIn } from "@lucide/vue";
import { avatarCropMetrics, decodeAvatarFile, renderAvatarCrop } from "../lib/local-profile.js";

const props=defineProps({
  open:{type:Boolean,default:false},
  file:{type:Object,default:null},
});
const emit=defineEmits(["apply","close"]);
const dialog=ref(null), stage=ref(null);
const loading=ref(false), applying=ref(false), dragging=ref(false), error=ref("");
const bitmap=shallowRef(null);
const previewUrl=ref(""), viewportSize=ref(300), zoom=ref(1), offsetX=ref(0), offsetY=ref(0);
let loadVersion=0, previousFocus=null, drag=null;

const metrics=computed(()=>bitmap.value ? avatarCropMetrics(bitmap.value,{
  viewportSize:viewportSize.value,
  zoom:zoom.value,
  offsetX:offsetX.value,
  offsetY:offsetY.value,
}) : {
  renderedWidth:viewportSize.value,
  renderedHeight:viewportSize.value,
  offsetX:0,
  offsetY:0,
});
const imageStyle=computed(()=>({
  width:`${metrics.value.renderedWidth}px`,
  height:`${metrics.value.renderedHeight}px`,
  transform:`translate(-50%, -50%) translate(${metrics.value.offsetX}px, ${metrics.value.offsetY}px)`,
}));
const zoomPercent=computed(()=>Math.round(zoom.value*100));

function releaseImage() {
  bitmap.value?.close?.();
  bitmap.value=null;
  if(previewUrl.value) URL.revokeObjectURL(previewUrl.value);
  previewUrl.value="";
}

function syncOffsets() {
  if(!bitmap.value)return;
  const next=avatarCropMetrics(bitmap.value,{
    viewportSize:viewportSize.value,
    zoom:zoom.value,
    offsetX:offsetX.value,
    offsetY:offsetY.value,
  });
  offsetX.value=next.offsetX;
  offsetY.value=next.offsetY;
}

function measureStage() {
  const width=stage.value?.getBoundingClientRect().width || 0;
  if(width>0)viewportSize.value=width;
  syncOffsets();
}

async function loadCrop() {
  const version=++loadVersion;
  releaseImage();
  error.value="";
  loading.value=false;
  applying.value=false;
  dragging.value=false;
  drag=null;
  zoom.value=1;
  offsetX.value=0;
  offsetY.value=0;
  if(!props.open || !props.file)return;
  previousFocus=document.activeElement;
  loading.value=true;
  try {
    const decoded=await decodeAvatarFile(props.file);
    if(version!==loadVersion || !props.open) {
      decoded?.close?.();
      return;
    }
    bitmap.value=decoded;
    previewUrl.value=URL.createObjectURL(props.file);
    await nextTick();
    measureStage();
    dialog.value?.focus();
  } catch(failure) {
    if(version===loadVersion)error.value=failure.message;
  } finally {
    if(version===loadVersion)loading.value=false;
  }
}

function close() {
  if(applying.value)return;
  ++loadVersion;
  releaseImage();
  emit("close");
  nextTick(()=>previousFocus?.focus?.());
}

function startDrag(event) {
  if(!bitmap.value || loading.value || applying.value || !event.isPrimary)return;
  dragging.value=true;
  drag={pointerId:event.pointerId,x:event.clientX,y:event.clientY,offsetX:offsetX.value,offsetY:offsetY.value};
  stage.value?.setPointerCapture?.(event.pointerId);
  event.preventDefault();
}

function moveDrag(event) {
  if(!dragging.value || drag?.pointerId!==event.pointerId)return;
  offsetX.value=drag.offsetX+event.clientX-drag.x;
  offsetY.value=drag.offsetY+event.clientY-drag.y;
  syncOffsets();
}

function endDrag(event) {
  if(drag?.pointerId!==event.pointerId)return;
  dragging.value=false;
  stage.value?.releasePointerCapture?.(event.pointerId);
  drag=null;
}

function nudge(event) {
  const amount=event.shiftKey?18:6;
  const changes={
    ArrowLeft:[-amount,0],ArrowRight:[amount,0],
    ArrowUp:[0,-amount],ArrowDown:[0,amount],
  };
  if(!changes[event.key])return;
  event.preventDefault();
  offsetX.value+=changes[event.key][0];
  offsetY.value+=changes[event.key][1];
  syncOffsets();
}

function apply() {
  if(!bitmap.value || loading.value || applying.value)return;
  applying.value=true;
  error.value="";
  try {
    const avatar=renderAvatarCrop(bitmap.value,{
      viewportSize:viewportSize.value,
      zoom:zoom.value,
      offsetX:offsetX.value,
      offsetY:offsetY.value,
    });
    emit("apply",avatar);
    nextTick(()=>previousFocus?.focus?.());
  } catch(failure) {
    error.value=failure.message;
    applying.value=false;
  }
}

function trapFocus(event) {
  if(event.key!=="Tab")return;
  const controls=[...dialog.value.querySelectorAll("button:not(:disabled),input:not(:disabled)")];
  if(!controls.length)return;
  const first=controls[0], last=controls.at(-1);
  if(event.shiftKey && (document.activeElement===first || document.activeElement===dialog.value)) {
    event.preventDefault();
    last.focus();
  } else if(!event.shiftKey && document.activeElement===last) {
    event.preventDefault();
    first.focus();
  }
}

watch([()=>props.open,()=>props.file],loadCrop,{immediate:true});
watch(zoom,syncOffsets);
onMounted(()=>window.addEventListener("resize",measureStage));
onBeforeUnmount(()=>{
  ++loadVersion;
  releaseImage();
  window.removeEventListener("resize",measureStage);
});
</script>

<template>
  <Teleport to="body">
    <Transition name="dialog">
      <div v-if="open" class="modal-backdrop avatar-crop-backdrop" @mousedown.self="close">
        <section ref="dialog" class="modal-card avatar-crop-dialog" role="dialog" aria-modal="true" aria-labelledby="avatar-crop-title" tabindex="-1" @keydown="trapFocus" @keydown.esc.stop.prevent="close">
          <header class="avatar-crop-heading">
            <h3 id="avatar-crop-title">调整头像</h3>
            <button class="icon-button" type="button" title="关闭" aria-label="关闭头像裁剪" @click="close"><X :size="17" /></button>
          </header>
          <div class="avatar-crop-body">
            <div v-if="loading" class="avatar-crop-loading"><LoaderCircle class="spinning" :size="22" /></div>
            <div v-else-if="error && !previewUrl" class="avatar-crop-load-error" role="alert">{{ error }}</div>
            <template v-else>
              <div ref="stage" class="avatar-crop-stage" :class="{dragging}" tabindex="0" role="group" aria-label="头像裁剪区域，可拖动调整位置" @pointerdown="startDrag" @pointermove="moveDrag" @pointerup="endDrag" @pointercancel="endDrag" @keydown="nudge">
                <img v-if="previewUrl" :src="previewUrl" :style="imageStyle" alt="" draggable="false">
              </div>
              <label class="avatar-crop-zoom">
                <ZoomIn :size="15" />
                <input v-model.number="zoom" type="range" min="1" max="3" step="0.01" aria-label="头像缩放比例">
                <output>{{ zoomPercent }}%</output>
              </label>
              <p v-if="error" class="avatar-crop-error" role="alert">{{ error }}</p>
            </template>
          </div>
          <footer class="avatar-crop-actions">
            <button class="button light" type="button" :disabled="applying" @click="close">取消</button>
            <button class="button dark" type="button" :disabled="loading || applying || !bitmap" @click="apply">
              <LoaderCircle v-if="applying" class="spinning" :size="14" />
              <Crop v-else :size="14" />应用头像
            </button>
          </footer>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.avatar-crop-backdrop {
  z-index: var(--ds-z-dialog);
}

.avatar-crop-dialog {
  width: min(420px, calc(100vw - 48px));
  padding: 0;
  overflow: hidden;
}

.avatar-crop-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 56px;
  border-bottom: 1px solid var(--line);
  padding: 0 18px 0 22px;
}

.avatar-crop-heading h3 {
  margin: 0;
  color: var(--ink);
  font-size: var(--ds-text-lg);
  font-weight: 600;
}

.avatar-crop-body {
  display: grid;
  justify-items: center;
  gap: 18px;
  min-height: 344px;
  padding: 22px;
}

.avatar-crop-stage {
  position: relative;
  width: min(300px, calc(100vw - 96px));
  aspect-ratio: 1;
  overflow: hidden;
  border: 1px solid rgba(255,255,255,.18);
  border-radius: 50%;
  background: var(--ds-viewer-canvas);
  box-shadow: inset 0 0 0 1px rgba(255,255,255,.08);
  cursor: grab;
  touch-action: none;
  user-select: none;
}

.avatar-crop-stage:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 3px;
}

.avatar-crop-stage.dragging {
  cursor: grabbing;
}

.avatar-crop-stage img {
  position: absolute;
  top: 50%;
  left: 50%;
  max-width: none;
  pointer-events: none;
  user-select: none;
}

.avatar-crop-zoom {
  display: grid;
  grid-template-columns: auto minmax(0,1fr) 48px;
  align-items: center;
  gap: 10px;
  width: min(300px, calc(100vw - 96px));
  color: var(--muted);
}

.avatar-crop-zoom input {
  width: 100%;
  accent-color: var(--accent);
}

.avatar-crop-zoom output {
  color: var(--ink);
  font-size: var(--ds-text-sm);
  font-variant-numeric: tabular-nums;
  text-align: right;
}

.avatar-crop-loading,
.avatar-crop-load-error {
  display: grid;
  place-items: center;
  align-self: center;
  min-height: 220px;
  color: var(--muted);
  text-align: center;
}

.avatar-crop-load-error,
.avatar-crop-error {
  color: var(--red);
  font-size: var(--ds-text-sm);
  line-height: 1.6;
}

.avatar-crop-error {
  margin: -6px 0 0;
}

.avatar-crop-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  border-top: 1px solid var(--line);
  padding: 14px 18px;
  background: var(--surface-muted);
}

.spinning {
  animation: icon-spin 1s linear infinite;
}

@media (prefers-reduced-motion: reduce) {
  .spinning {
    animation: none;
  }
}
</style>
