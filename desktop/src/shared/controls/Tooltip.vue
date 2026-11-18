<script setup>
import { computed, onBeforeUnmount, ref } from "vue";

const props = defineProps({
  text: { type: String, default: "" },
  placement: { type: String, default: "top", validator: value => ["top", "right", "bottom", "left"].includes(value) },
  disabled: Boolean,
});

const visible = ref(false);
let hideTimer;
const tooltipId = `mumu-tooltip-${Math.random().toString(36).slice(2, 10)}`;
const describedBy = computed(() => props.text && !props.disabled && visible.value ? tooltipId : undefined);

function show() {
  if (props.disabled || !props.text) return;
  clearTimeout(hideTimer);
  visible.value = true;
}
function hide() {
  clearTimeout(hideTimer);
  hideTimer = setTimeout(() => { visible.value = false; }, 80);
}
function cancelHide() {
  clearTimeout(hideTimer);
}
onBeforeUnmount(() => clearTimeout(hideTimer));
</script>

<template>
  <span class="ui-tooltip" :class="[`placement-${placement}`, { visible, disabled }]" @mouseenter="show" @mouseleave="hide">
    <span class="ui-tooltip-trigger" :aria-describedby="describedBy" @focusin="show" @focusout="hide" @keydown.escape="hide">
      <slot />
    </span>
    <span v-if="visible && text && !disabled" :id="tooltipId" class="ui-tooltip-content" role="tooltip" @mouseenter="cancelHide" @mouseleave="hide">{{ text }}</span>
  </span>
</template>

<style scoped>
.ui-tooltip { position: relative; display: inline-flex; max-width: 100%; vertical-align: middle; }
.ui-tooltip-trigger { display: inline-flex; min-width: 0; max-width: 100%; }
.ui-tooltip-content { position: absolute; z-index: 180; width: max-content; max-width: min(280px, 42vw); border: 1px solid var(--line-strong); border-radius: var(--radius-control); background: var(--ink); color: var(--surface); padding: 6px 8px; font-size: var(--text-xs); line-height: 1.45; overflow-wrap: anywhere; pointer-events: auto; box-shadow: var(--shadow-menu); }
.ui-tooltip-content::after { position: absolute; width: 7px; height: 7px; border: 1px solid var(--ink); background: var(--ink); content: ""; transform: rotate(45deg); }
.placement-top .ui-tooltip-content { left: 50%; bottom: calc(100% + 9px); transform: translateX(-50%); }
.placement-top .ui-tooltip-content::after { left: 50%; bottom: -5px; margin-left: -4px; border-top: 0; border-left: 0; }
.placement-right .ui-tooltip-content { left: calc(100% + 9px); top: 50%; transform: translateY(-50%); }
.placement-right .ui-tooltip-content::after { left: -5px; top: 50%; margin-top: -4px; border-top: 0; border-right: 0; }
.placement-bottom .ui-tooltip-content { left: 50%; top: calc(100% + 9px); transform: translateX(-50%); }
.placement-bottom .ui-tooltip-content::after { left: 50%; top: -5px; margin-left: -4px; border-right: 0; border-bottom: 0; }
.placement-left .ui-tooltip-content { right: calc(100% + 9px); top: 50%; transform: translateY(-50%); }
.placement-left .ui-tooltip-content::after { right: -5px; top: 50%; margin-top: -4px; border-left: 0; border-bottom: 0; }
@media (prefers-reduced-motion: reduce) { .ui-tooltip-content { transition: none; } }
</style>
