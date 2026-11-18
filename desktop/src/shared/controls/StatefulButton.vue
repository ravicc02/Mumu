<script setup>
import { computed } from "vue";
import { Check, CircleAlert, LoaderCircle } from "@lucide/vue";

const props = defineProps({
  label: { type: String, required: true },
  pendingLabel: { type: String, default: "处理中…" },
  successLabel: { type: String, default: "已完成" },
  errorLabel: { type: String, default: "操作失败，重试" },
  status: { type: String, default: "idle", validator: value => ["idle", "pending", "success", "error", "disabled"].includes(value) },
  icon: { type: [Object, Function], default: null },
  pendingIcon: { type: [Object, Function], default: null },
  variant: { type: String, default: "dark", validator: value => ["dark", "light", "ghost", "danger"].includes(value) },
  disabled: Boolean,
  type: { type: String, default: "button" },
  onRetry: { type: Function, default: null },
});
const emit = defineEmits(["click", "retry"]);
const isDisabled = computed(() => props.disabled || props.status === "pending" || props.status === "disabled");
const currentLabel = computed(() => ({ pending: props.pendingLabel, success: props.successLabel, error: props.errorLabel }[props.status] || props.label));
const currentIcon = computed(() => ({ pending: props.pendingIcon || LoaderCircle, success: Check, error: CircleAlert }[props.status] || props.icon));
function activate(event) {
  if (isDisabled.value) return;
  if (props.status === "error") {
    emit("retry", event);
    props.onRetry?.(event);
    return;
  }
  emit("click", event);
}
</script>

<template>
  <button class="button stateful-button" :class="[variant, `state-${status}`]" :type="type" :disabled="isDisabled" :aria-busy="status === 'pending' || undefined" @click="activate">
    <component :is="currentIcon" v-if="currentIcon" :size="15" aria-hidden="true" :class="{ spinning: status === 'pending' }" />
    <span>{{ currentLabel }}</span>
  </button>
</template>

<style scoped>
.stateful-button { min-width: 0; }
.stateful-button.state-success { background: var(--green); color: var(--on-accent); }
.stateful-button.state-error { background: var(--red); color: var(--on-accent); }
.stateful-button.state-disabled { cursor: not-allowed; }
.spinning { animation: icon-spin 1s linear infinite; }
@media (prefers-reduced-motion: reduce) { .spinning { animation: none; } }
</style>
