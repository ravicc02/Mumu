<script setup>
import { computed, nextTick, ref, watch } from "vue";
import { Check, CircleAlert, LoaderCircle, LogIn, RefreshCw, X } from "@lucide/vue";
import PlatformLogo from "../../platforms/components/PlatformLogo.vue";
import AccountAvatar from "./AccountAvatar.vue";
const props = defineProps({ open: Boolean, state: { type: Object, required: true }, platformLabel: { type: String, default: "学习通" } });
const emit = defineEmits(["close", "retry"]);
const dialog = ref(null), previousFocus = ref(null);
const pending = computed(() => ["opening", "waiting", "verifying"].includes(props.state.status));
const title = computed(() => ({ opening: "正在打开登录窗口", waiting: "等待授权", verifying: "正在同步账号资料", error: "授权未完成", success: props.state.message || "添加成功" })[props.state.status] || "授权");
const stage = computed(() => props.state.status === "success" ? 3 : props.state.status === "verifying" ? 1 : 0);
watch(() => props.open, async open => {
  if (open) { previousFocus.value = document.activeElement; await nextTick(); dialog.value?.focus(); }
  else if (previousFocus.value?.isConnected) previousFocus.value.focus();
}, { immediate: true });
function trapFocus(event) {
  if (event.key !== "Tab") return;
  const controls = [...dialog.value.querySelectorAll("button:not(:disabled)")];
  const first = controls[0], last = controls.at(-1);
  if (event.shiftKey && [first, dialog.value].includes(document.activeElement)) { event.preventDefault(); last?.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
}
</script>

<template>
  <Transition name="dialog"><div v-if="open" class="modal-backdrop authorization-backdrop" @mousedown.self="emit('close')" @keydown.esc.stop="emit('close')">
    <section ref="dialog" class="modal-card modal-narrow account-authorization" role="dialog" aria-modal="true" aria-labelledby="account-authorization-title" :aria-busy="pending" :data-state="state.status" tabindex="-1" @keydown="trapFocus">
      <header class="authorization-heading"><span><PlatformLogo :platform="state.platform" :size="26" />{{ platformLabel }}</span><button class="icon-button" type="button" aria-label="关闭授权进度" title="关闭" @click="emit('close')"><X :size="17" /></button></header>
      <div class="authorization-body" aria-live="polite" aria-atomic="true">
        <div class="authorization-status-icon" :class="state.status"><Check v-if="state.status === 'success'" :size="27" /><CircleAlert v-else-if="state.status === 'error'" :size="27" /><LoaderCircle v-else :size="27" class="spinning" /></div>
        <h2 id="account-authorization-title">{{ title }}</h2>
        <div v-if="state.status === 'success' && state.account" class="authorized-account"><AccountAvatar :account="state.account" /><strong>{{ state.account.name }}</strong><Check :size="15" /></div>
        <p v-else-if="state.status === 'error'" class="authorization-error" role="alert">{{ state.error }}</p>
        <p v-else class="authorization-hint">{{ state.status === 'opening' ? '正在连接授权浏览器' : state.status === 'verifying' ? '正在获取名称和头像' : '请在登录窗口完成授权' }}</p>
      </div>
      <ol class="authorization-stages" aria-label="授权进度"><li v-for="(label, index) in ['登录授权', '同步资料', state.mode === 'update' ? '更新账号' : '添加账号']" :key="label" :class="{ done: index < stage, current: index === stage && pending }"><span><Check v-if="index < stage" :size="11" /><span v-else>{{ index + 1 }}</span></span>{{ label }}</li></ol>
      <footer class="modal-actions"><button v-if="state.status === 'error'" class="button light" type="button" @click="emit('close')">关闭</button><button v-if="state.status === 'error'" class="button dark" type="button" @click="emit('retry')"><RefreshCw :size="14" />重新授权</button><button v-else-if="state.status === 'success'" class="button dark" type="button" @click="emit('close')"><Check :size="14" />完成</button><button v-else class="button light" type="button" @click="emit('close')"><LogIn :size="14" />后台等待</button></footer>
    </section>
  </div></Transition>
</template>

<style scoped>
/* finesse · component: account-authorization · register=product
 * states: default · hover · focus-visible · active · disabled · loading · error · success
 * tokens: inherited from workbench.css */
.authorization-backdrop { z-index: 75; }
.account-authorization { padding: 22px 24px; outline: none; }
.authorization-heading,.authorization-heading > span { display: flex; align-items: center; gap: 10px; }
.authorization-heading { justify-content: space-between; color: var(--muted); font-size: 13px; }
.authorization-body { min-height: 208px; display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 22px 0; gap: 15px; }
.authorization-status-icon { display: grid; place-items: center; width: 56px; height: 56px; border: 1px solid var(--line); border-radius: 8px; color: var(--accent); background: var(--accent-soft); }
.authorization-status-icon.success { color: var(--green); background: var(--green-soft); }
.authorization-status-icon.error { color: var(--red); background: var(--red-soft); }
.authorization-body h2 { font-size: 20px; line-height: 1.5; text-align: center; }
.authorization-hint,.authorization-error { min-height: 20px; color: var(--muted); font-size: 12px; line-height: 1.7; text-align: center; overflow-wrap: anywhere; }
.authorization-error { color: var(--red); }
.authorized-account { display: flex; align-items: center; gap: 10px; font-size: 14px; }
.authorized-account .account-avatar { width: 32px; height: 32px; }
.authorized-account > svg { color: var(--green); }
.authorization-stages { display: flex; align-items: center; justify-content: space-between; list-style: none; padding: 0; gap: 8px; }
.authorization-stages li { display: flex; align-items: center; gap: 6px; color: var(--muted); font-size: 11px; white-space: nowrap; }
.authorization-stages li > span { display: grid; place-items: center; width: 20px; height: 20px; border: 1px solid var(--line); border-radius: 4px; font-size: 10px; }
.authorization-stages .current { color: var(--accent); }
.authorization-stages .current > span { background: var(--accent-soft); border-color: var(--accent); }
.authorization-stages .done { color: var(--green); }
.authorization-stages .done > span { background: var(--green-soft); }
.account-authorization button { outline: 2px solid transparent; outline-offset: 3px; }
.account-authorization button:focus-visible { outline-color: var(--accent); }
.account-authorization button:active { transform: translateY(1px); }
.account-authorization button:disabled { opacity: .45; cursor: not-allowed; }
@media (hover: hover) { .account-authorization .button.light:hover { background: var(--surface-muted); } }
@media (prefers-reduced-motion: reduce) { .account-authorization .spinning { animation: none; } .account-authorization button { transition: none; } }
</style>
