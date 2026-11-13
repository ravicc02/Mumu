<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { ExternalLink, LoaderCircle, Maximize2, RefreshCw } from "@lucide/vue";
import { api } from "../../../app/api.js";
import { conciseMessage } from "../lib/presentation.js";

const props = defineProps({ runId: { type: String, required: true }, kind: { type: String, default: "learning" }, nativeBrowser: { type: Boolean, default: true } });
const emit = defineEmits(["open-browser"]);
const canvas = ref(null), keyboard = ref(null), root = ref(null);
const viewport = ref(null), available = ref({ width: 1440, height: 900 }), frameSize = ref({ width: 1440, height: 900 });
const canvasStyle = computed(() => {
  const ratio = frameSize.value.width / frameSize.value.height;
  const width = Math.min(available.value.width, available.value.height * ratio);
  return { width: width + "px", height: width / ratio + "px" };
});
let observer;
const state = ref("connecting"), message = ref(""), url = ref(""), viewId = ref(null), paintedFrame = ref(0);
let renderGeneration = 0;
const address = computed(() => { try { const value = new URL(url.value); return value.hostname + value.pathname; } catch { return ""; } });
let socket, generation = 0, disposed = false, decoding = false, pendingFrame = null, movedAt = 0, connectionTimer;

function disconnect() {
  generation++;
  pendingFrame = null;
  decoding = false;
  renderGeneration++;
  viewId.value = null;
  clearTimeout(connectionTimer);
  if (socket) { socket.onclose = null; socket.close(); socket = null; }
}
function armFrameTimeout(token) {
  clearTimeout(connectionTimer);
  connectionTimer=setTimeout(()=>{
    if(token!==generation || state.value!=="connecting") return;
    state.value="disconnected";message.value="浏览器画面连接超时";disconnect();
  },10000);
}
async function connect() {
  disconnect();
  const token = generation;
  state.value = "connecting"; message.value = "";
  try {
    const ticket = await api.browserSession(props.kind, props.runId);
    if (disposed || token !== generation) return;
    socket = new WebSocket(api.browserUrl(props.kind, props.runId));
    const current = socket;
    armFrameTimeout(token);
    current.onopen = () => current.send(JSON.stringify(ticket));
    current.onmessage = event => {
      if (disposed || token !== generation) return;
      let payload;
      try { payload=JSON.parse(event.data); if(!payload || typeof payload!=="object" || typeof payload.type!=="string") throw new Error("Invalid frame"); }
      catch { state.value="disconnected"; message.value="浏览器数据格式错误，请重新连接"; disconnect(); return; }
      if (payload.type === "frame") { pendingFrame = payload; paint(token); }
      else if (payload.type === "closed") { state.value = "closed"; message.value = conciseMessage(payload.message); disconnect(); }
      else if (payload.type === "switching") {
        renderGeneration++; pendingFrame=null; decoding=false; viewId.value=null;
        state.value="connecting"; message.value=conciseMessage(payload.message); armFrameTimeout(token);
      }
      else if (payload.type === "input_unavailable") message.value=conciseMessage(payload.message);
    };
    current.onclose = () => {
      if (disposed || token !== generation || state.value === "closed") return;
      state.value = "disconnected"; message.value = "实时浏览器连接已断开"; disconnect();
    };
    current.onerror = () => { if (token === generation) { state.value="disconnected"; message.value = "无法连接实时浏览器"; disconnect(); } };
  } catch (error) {
    if (!disposed && token === generation) { state.value = "disconnected"; message.value = conciseMessage(error.message); }
  }
}
function paint(token) {
  if (decoding || !pendingFrame) return;
  const frame = pendingFrame; pendingFrame = null; decoding = true;
  const renderToken=renderGeneration;
  const image = new Image();
  image.onload = () => {
    if (disposed || token !== generation || renderToken !== renderGeneration) return;
    const element = canvas.value;
    if (element.width !== image.naturalWidth) element.width = image.naturalWidth;
    if (element.height !== image.naturalHeight) element.height = image.naturalHeight;
    frameSize.value = { width: image.naturalWidth, height: image.naturalHeight };
    element.getContext("2d").drawImage(image, 0, 0);
    clearTimeout(connectionTimer);
    url.value = frame.url; viewId.value=frame.view_id ?? null; paintedFrame.value++;
    message.value=""; state.value = "connected";
    decoding = false; paint(token);
  };
  image.onerror = () => {
    if (disposed || token !== generation || renderToken !== renderGeneration) return;
    decoding = false; state.value = "disconnected"; message.value = "实时画面解码失败"; disconnect();
  };
  image.src = `data:image/jpeg;base64,${frame.image}`;
}
function send(payload) { if (socket?.readyState === WebSocket.OPEN && state.value === "connected") socket.send(JSON.stringify({ ...payload, ...(viewId.value == null ? {} : { view_id:viewId.value }) })); }
function modifiers(event) { return (event.altKey ? 1 : 0) | (event.ctrlKey ? 2 : 0) | (event.metaKey ? 4 : 0) | (event.shiftKey ? 8 : 0); }
function position(event) {
  const bounds = canvas.value.getBoundingClientRect();
  return { x: Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width)), y: Math.max(0, Math.min(1, (event.clientY - bounds.top) / bounds.height)) };
}
function pointer(event, action) {
  if (state.value !== "connected") return;
  if (action === "mouseMoved" && !event.buttons && performance.now() - movedAt < 40) return;
  movedAt = performance.now();
  if (action === "mousePressed") { event.preventDefault(); keyboard.value.focus({ preventScroll: true }); canvas.value.setPointerCapture(event.pointerId); }
  send({ type: "pointer", action, ...position(event), button: action === "mouseMoved" ? "none" : ["left", "middle", "right"][event.button] || "none", buttons: event.buttons, modifiers: modifiers(event) });
  if (action === "mouseReleased" && canvas.value.hasPointerCapture(event.pointerId)) canvas.value.releasePointerCapture(event.pointerId);
}
function wheel(event) {
  const scale = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? 480 : 1;
  send({ type: "wheel", ...position(event), delta_x: Math.max(-2000, Math.min(2000, event.deltaX * scale)), delta_y: Math.max(-2000, Math.min(2000, event.deltaY * scale)), modifiers: modifiers(event) });
}
function key(event, action) {
  if (event.isComposing || event.keyCode === 229) return;
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "v") return;
  if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) return;
  event.preventDefault();
  send({ type: "key", action, key: event.key, code: event.code, key_code: event.keyCode, modifiers: modifiers(event) });
}
function text(event) {
  if (event.isComposing) return;
  if (event.target.value) send({ type: "text", text: event.target.value });
  event.target.value = "";
}
function paste(event) { event.preventDefault(); const value = event.clipboardData.getData("text/plain"); if (value) send({ type: "text", text: value.slice(0, 10000) }); }
async function expand() {
  try {
    if (document.fullscreenElement) await document.exitFullscreen();
    else await root.value.requestFullscreen();
  } catch { message.value = "未能打开全屏，可直接在当前实时画面中操作"; }
}
defineExpose({ expand });
onMounted(() => {
  observer = new ResizeObserver(entries => {
    const rect = entries[0].contentRect;
    available.value = { width: rect.width, height: rect.height };
  });
  observer.observe(viewport.value);
  connect();
});
watch(() => [props.kind, props.runId], connect);
onUnmounted(() => { disposed = true; observer?.disconnect(); disconnect(); });
</script>

<template>
  <div ref="root" class="live-browser" :data-stream-state="state">
    <div class="live-browser-toolbar">
      <span class="live-browser-state" :class="state"><i></i>{{ state === 'connected' ? '实时连接' : state === 'connecting' ? '连接中' : '已断开' }}</span>
      <span v-if="message && state === 'connected'" class="live-browser-input-feedback" role="status">{{ message }}</span>
      <span class="live-browser-address" :title="address">{{ address }}</span>
      <button class="icon-button" type="button" title="重新连接画面" aria-label="重新连接实时浏览器" @click="connect"><RefreshCw :size="14" /></button>
      <button class="icon-button" type="button" title="全屏浏览器" aria-label="全屏实时浏览器" @click="expand"><Maximize2 :size="14" /></button>
      <button v-if="nativeBrowser" class="icon-button" type="button" title="进入原浏览器" aria-label="进入原浏览器" @click="emit('open-browser')"><ExternalLink :size="14" /></button>
    </div>
    <div ref="viewport" class="live-browser-viewport">
      <canvas ref="canvas" :style="canvasStyle" :data-frame-sequence="paintedFrame" :data-view-id="viewId" width="1440" height="900" aria-label="实时浏览器" @pointerdown="pointer($event, 'mousePressed')" @pointerup="pointer($event, 'mouseReleased')" @pointermove="pointer($event, 'mouseMoved')" @pointercancel="pointer($event, 'mouseReleased')" @wheel.prevent="wheel" @contextmenu.prevent></canvas>
      <textarea ref="keyboard" class="live-browser-keyboard" aria-label="浏览器键盘输入" autocomplete="off" autocapitalize="off" spellcheck="false" @keydown="key($event, 'keyDown')" @keyup="key($event, 'keyUp')" @input="text" @compositionend="text" @paste="paste"></textarea>
      <div v-if="state !== 'connected'" class="live-browser-feedback" role="status"><LoaderCircle v-if="state === 'connecting'" class="spinning" :size="22" /><template v-else><span>{{ message || '实时浏览器已断开' }}</span><button class="button light" type="button" @click="connect"><RefreshCw :size="14" />重新连接</button></template></div>
    </div>
  </div>
</template>

<style scoped>
.live-browser { display: flex; flex-direction: column; min-height: 0; height: 100%; background: var(--surface, white); }
.live-browser-toolbar { display: flex; align-items: center; gap: 6px; min-height: 36px; padding: 3px 8px; border-bottom: 1px solid var(--line); flex: none; }
.live-browser-toolbar .icon-button { width: 27px; height: 27px; flex: none; }
.live-browser-state { display: inline-flex; align-items: center; gap: 5px; flex: none; color: var(--muted); font-size: 11px; }
.live-browser-state i { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.live-browser-state.connected { color: var(--green); }
.live-browser-input-feedback { color: var(--orange); font-size: 11px; }
.live-browser-address { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--muted); font-size: 10px; }
.live-browser-viewport { position: relative; display: flex; align-items: center; justify-content: center; flex: 1; min-height: 0; overflow: hidden; overscroll-behavior: contain; background: var(--surface-muted, #e9ecef); }
.live-browser-viewport canvas { display: block; width: 100%; height: 100%; object-fit: contain; touch-action: none; cursor: default; }
.live-browser-keyboard { position: absolute; opacity: 0; width: 1px; height: 1px; padding: 0; border: 0; left: 0; top: 0; resize: none; }
.live-browser-feedback { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 14px; background: var(--surface, white); color: var(--muted); font-size: 12px; }
.live-browser:fullscreen .live-browser-viewport { display: flex; justify-content: center; align-items: center; background: #151719; }
.live-browser:fullscreen canvas { width: auto; max-width: 100%; max-height: 100%; object-fit: contain; }
</style>
