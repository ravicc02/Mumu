<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";

let nextId = 0;
const props = defineProps({
  modelValue: Boolean,
  title: { type: String, default: "" },
  size: { type: String, default: "medium", validator: value => ["small", "medium", "large", "wide"].includes(value) },
  closeOnEsc: { type: Boolean, default: true },
  closeOnBackdrop: { type: Boolean, default: true },
  busy: Boolean,
  allowCloseWhileBusy: Boolean,
  closeLabel: { type: String, default: "关闭" },
});
const emit = defineEmits(["update:modelValue", "close"]);
const dialog = ref(null);
const titleId = `modal-shell-title-${++nextId}`;
let restoreTarget = null;

const focusableSelector = [
  "a[href]", "button:not([disabled])", "textarea:not([disabled])", "input:not([disabled])", "select:not([disabled])", "[tabindex]:not([tabindex='-1'])",
].join(",");
function focusInitial() {
  const target = dialog.value?.querySelector("[autofocus], input:not([disabled]), button:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])") || dialog.value;
  target?.focus?.();
}
function restoreFocus() {
  const target = restoreTarget;
  restoreTarget = null;
  if (target && target.isConnected && typeof target.focus === "function") nextTick(() => target.focus());
}
async function openDialog() {
  if (typeof document !== "undefined" && !restoreTarget) restoreTarget = document.activeElement;
  await nextTick();
  focusInitial();
}
function close(reason = "close") {
  if (props.busy && !props.allowCloseWhileBusy) return;
  emit("update:modelValue", false);
  emit("close", reason);
  restoreFocus();
}
function onBackdrop(event) {
  if (props.closeOnBackdrop && event.target === event.currentTarget) close("backdrop");
}
function onKeydown(event) {
  if (event.key === "Escape") {
    if (props.closeOnEsc) { event.preventDefault(); close("escape"); }
    return;
  }
  if (event.key !== "Tab" || !dialog.value) return;
  const items = [...dialog.value.querySelectorAll(focusableSelector)].filter(item => item.offsetParent !== null);
  if (!items.length) { event.preventDefault(); dialog.value.focus(); return; }
  const first = items[0];
  const last = items.at(-1);
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
}
watch(() => props.modelValue, value => { if (value) openDialog(); else restoreFocus(); });
onMounted(() => { if (props.modelValue) openDialog(); });
onBeforeUnmount(() => restoreFocus());
</script>

<template>
  <Teleport to="body">
    <Transition name="modal-shell">
      <div v-if="modelValue" class="modal-shell-backdrop" @mousedown="onBackdrop" @keydown="onKeydown">
        <section ref="dialog" class="modal-shell-card" :class="`size-${size}`" role="dialog" aria-modal="true" tabindex="-1" :aria-labelledby="title ? titleId : undefined" :aria-busy="busy || undefined" @mousedown.stop>
          <header v-if="title || $slots.title" class="modal-shell-heading">
            <h2 v-if="title" :id="titleId">{{ title }}</h2>
            <slot v-else name="title" />
            <button class="modal-shell-close" type="button" :aria-label="closeLabel" :disabled="busy && !allowCloseWhileBusy" @click="close('button')">×</button>
          </header>
          <button v-else class="modal-shell-close standalone" type="button" :aria-label="closeLabel" :disabled="busy && !allowCloseWhileBusy" @click="close('button')">×</button>
          <div class="modal-shell-content"><slot /></div>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.modal-shell-backdrop { position: fixed; inset: 0; z-index: 120; display: grid; place-items: center; padding: 24px; background: var(--overlay); }
.modal-shell-card { position: relative; width: min(520px, calc(100vw - 48px)); max-height: calc(100dvh - 48px); overflow-y: auto; border: 1px solid var(--line-strong); border-radius: var(--radius-panel); background: var(--surface); box-shadow: var(--shadow-modal); padding: 24px; }
.modal-shell-card.size-small { width: min(420px, calc(100vw - 48px)); }
.modal-shell-card.size-large { width: min(760px, calc(100vw - 48px)); }
.modal-shell-card.size-wide { width: min(1040px, calc(100vw - 48px)); }
.modal-shell-heading { display: flex; align-items: start; justify-content: space-between; gap: 16px; margin-bottom: 20px; }
.modal-shell-heading h2 { min-width: 0; margin: 0; color: var(--ink); font-size: 20px; line-height: 1.4; font-weight: 500; overflow-wrap: anywhere; }
.modal-shell-content { min-width: 0; }
.modal-shell-close { display: inline-grid; place-items: center; flex: none; width: 32px; height: 32px; border: 0; border-radius: var(--radius-control); background: transparent; color: var(--muted); font-size: 24px; line-height: 1; }
.modal-shell-close:hover:not(:disabled) { background: var(--surface-muted); color: var(--ink); }
.modal-shell-close.standalone { position: absolute; top: 14px; right: 14px; }
.modal-shell-enter-active, .modal-shell-leave-active { transition: opacity 160ms ease; }
.modal-shell-enter-active .modal-shell-card, .modal-shell-leave-active .modal-shell-card { transition: transform 180ms var(--ease), opacity 140ms ease; }
.modal-shell-enter-from, .modal-shell-leave-to { opacity: 0; }
.modal-shell-enter-from .modal-shell-card, .modal-shell-leave-to .modal-shell-card { opacity: 0; transform: translateY(8px) scale(.985); }
@media (prefers-reduced-motion: reduce) { .modal-shell-enter-active, .modal-shell-leave-active, .modal-shell-enter-active .modal-shell-card, .modal-shell-leave-active .modal-shell-card { transition: none; } .modal-shell-enter-from .modal-shell-card, .modal-shell-leave-to .modal-shell-card { transform: none; } }
</style>
