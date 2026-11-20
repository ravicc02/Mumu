<script setup>
import { computed } from "vue";
import { CircleAlert, RefreshCw } from "@lucide/vue";
import WaterMascot from "./WaterMascot.vue";

const props = defineProps({
  progress: { type: Number, default: 0 },
  message: { type: String, default: "正在启动本地服务" },
  detail: { type: String, default: "" },
  error: { type: String, default: "" },
});
const emit = defineEmits(["retry"]);
const percent = computed(() => Math.max(0, Math.min(100, Math.round(props.progress))));
</script>

<template>
  <main class="launch-experience" :class="{ failed: Boolean(error) }" aria-busy="!error">
    <section class="launch-panel" aria-live="polite">
      <div class="launch-mascot">
        <WaterMascot mood="working" :water=".62" :paused="Boolean(error)" />
      </div>
      <div class="launch-copy">
        <span class="launch-product">Mumu</span>
        <h1>{{ error ? "启动未完成" : message }}</h1>
        <p v-if="error" class="launch-error"><CircleAlert :size="16" />{{ error }}</p>
      </div>
      <div
        class="launch-progress"
        :class="{ complete: percent === 100 && !error, failed: Boolean(error) }"
        role="progressbar"
        aria-label="Mumu 启动进度"
        aria-valuemin="0"
        aria-valuemax="100"
        :aria-valuenow="percent"
      >
        <span :style="{ transform: `scaleX(${percent / 100})` }"></span>
      </div>
      <div class="launch-meta">
        <span>{{ error ? "服务尚未就绪" : detail || "首次启动可能需要数秒" }}</span>
        <strong>{{ percent }}%</strong>
      </div>
      <button v-if="error" class="button dark launch-retry" type="button" @click="emit('retry')">
        <RefreshCw :size="15" />重新启动
      </button>
    </section>
  </main>
</template>

<style scoped>
.launch-experience {
  display: grid;
  place-items: center;
  width: 100%;
  height: 100dvh;
  min-height: 560px;
  background: var(--bg);
  padding: 40px;
}

.launch-panel {
  display: grid;
  justify-items: center;
  width: min(420px, 100%);
  text-align: center;
}

.launch-mascot {
  width: 184px;
  height: 184px;
  margin-bottom: 18px;
}

.launch-copy {
  display: grid;
  justify-items: center;
  min-height: 72px;
}

.launch-product {
  color: var(--ink);
  font-size: var(--text-sm);
  font-weight: 500;
  letter-spacing: 0;
}

.launch-copy h1 {
  margin-top: 8px;
  color: var(--ink);
  font-size: var(--ds-text-3xl);
  font-weight: 500;
  line-height: 1.4;
}

.launch-copy > p {
  max-width: 360px;
  margin-top: 8px;
  color: var(--muted);
  font-size: var(--text-sm);
  line-height: 1.7;
}

.launch-error {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  color: var(--red) !important;
  text-align: left;
}

.launch-error svg {
  flex: none;
  margin-top: 2px;
}

.launch-progress {
  position: relative;
  width: min(340px, 100%);
  height: 6px;
  margin-top: 10px;
  overflow: hidden;
  border-radius: 4px;
  background: var(--surface-muted);
}

.launch-progress > span {
  display: block;
  width: 100%;
  height: 100%;
  border-radius: inherit;
  background: var(--accent);
  transform-origin: left;
  transition: transform 380ms var(--ease);
}

.launch-progress.failed > span {
  background: var(--red);
}

.launch-meta {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  width: min(340px, 100%);
  margin-top: 8px;
  color: var(--muted);
  font-size: var(--text-xs);
}

.launch-meta strong {
  color: var(--ink);
  font-variant-numeric: tabular-nums;
  font-weight: 500;
}

.launch-retry {
  margin-top: 18px;
}

@media (max-width: 640px) {
  .launch-experience { padding: 24px; }
  .launch-mascot { width: 150px; height: 150px; }
}

@media (prefers-reduced-motion: reduce) {
  .launch-progress > span { transition: none; }
}
</style>
