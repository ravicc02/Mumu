<script setup>
import { computed } from "vue";
import { Check, CircleAlert, CircleDot, Clock3, LoaderCircle, Pause, ShieldCheck, UserRound, X } from "@lucide/vue";

const props = defineProps({
  status: { type: String, required: true },
  label: { type: String, default: "" },
  size: { type: String, default: "md", validator: value => ["sm", "md"].includes(value) },
  withIcon: { type: Boolean, default: true },
  pulse: Boolean,
});

const statusMap = {
  queued: { label: "排队中", tone: "neutral", icon: Clock3 },
  pending: { label: "等待中", tone: "neutral", icon: Clock3 },
  running: { label: "进行中", tone: "accent", icon: LoaderCircle },
  authorizing: { label: "授权中", tone: "warning", icon: LoaderCircle },
  paused: { label: "已暂停", tone: "neutral", icon: Pause },
  waiting: { label: "等待处理", tone: "warning", icon: Clock3 },
  awaiting_user: { label: "待介入", tone: "warning", icon: UserRound },
  needs_intervention: { label: "待介入", tone: "warning", icon: UserRound },
  incomplete: { label: "未完成", tone: "danger", icon: CircleAlert },
  interrupted: { label: "已中断", tone: "danger", icon: CircleAlert },
  failed: { label: "失败", tone: "danger", icon: X },
  revoked: { label: "已撤销", tone: "danger", icon: X },
  completed: { label: "已完成", tone: "success", icon: Check },
  ready: { label: "就绪", tone: "success", icon: ShieldCheck },
  enabled: { label: "已启用", tone: "success", icon: Check },
  disabled: { label: "未启用", tone: "neutral", icon: CircleDot },
};

const current = computed(() => statusMap[props.status] ?? { label: props.status || "未知状态", tone: "neutral", icon: CircleDot });
const text = computed(() => props.label || current.value.label);
const icon = computed(() => current.value.icon);
</script>

<template>
  <span class="ui-status-badge" :class="[`tone-${current.tone}`, `size-${size}`, { pulse }]" :data-status="status" :aria-label="text">
    <component :is="icon" v-if="withIcon" :size="size === 'sm' ? 12 : 13" aria-hidden="true" :class="{ spinning: status === 'running' || status === 'authorizing' }" />
    <span>{{ text }}</span>
  </span>
</template>

<style scoped>
.ui-status-badge { display: inline-flex; align-items: center; gap: 5px; width: fit-content; max-width: 100%; border: 1px solid transparent; border-radius: 999px; font-weight: 500; line-height: 1.35; white-space: nowrap; }
.ui-status-badge > span { overflow: hidden; text-overflow: ellipsis; }
.size-sm { padding: 2px 6px; font-size: var(--text-xs); }
.size-md { padding: 4px 8px; font-size: var(--text-xs); }
.tone-neutral { border-color: var(--line); background: var(--surface-muted); color: var(--muted); }
.tone-accent { border-color: rgba(49,87,213,.18); background: var(--accent-soft); color: var(--accent); }
.tone-success { border-color: rgba(27,117,80,.16); background: var(--green-soft); color: var(--green); }
.tone-warning { border-color: rgba(148,98,15,.18); background: var(--orange-soft); color: var(--orange); }
.tone-danger { border-color: rgba(182,65,59,.16); background: var(--red-soft); color: var(--red); }
.pulse:not(.tone-neutral) { animation: ui-status-pulse 1.8s ease-in-out infinite; }
.spinning { animation: icon-spin 1s linear infinite; }
@keyframes ui-status-pulse { 50% { box-shadow: 0 0 0 4px color-mix(in srgb, currentColor 12%, transparent); } }
@media (prefers-reduced-motion: reduce) { .pulse, .spinning { animation: none; } }
</style>
