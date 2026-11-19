<script setup>
import { computed, nextTick, ref, watch } from "vue";
import { X } from "@lucide/vue";
import changelog from "../../../../../CHANGELOG.md?raw";
import { displayVersion } from "../../../app/lib/product.js";
import { parseChangelog } from "../lib/changelog.js";

const props = defineProps({ open: Boolean });
const emit = defineEmits(["close"]);
const entries = parseChangelog(changelog).filter(entry => entry.version !== "Unreleased");
const selected = ref(displayVersion);
const current = computed(() => entries.find(entry => entry.version === selected.value) || entries[0]);
const dialog = ref(null);
let trigger;
watch(() => props.open, async open => {
  if (open) {
    trigger = document.activeElement;
    selected.value = entries.some(entry => entry.version === displayVersion) ? displayVersion : entries[0]?.version;
    await nextTick();
    dialog.value?.focus();
  } else if (trigger?.isConnected) trigger.focus();
});
function onKeydown(event) {
  if (event.key === "Escape") { event.stopPropagation(); emit("close"); return; }
  if (event.key !== "Tab") return;
  const controls = [...dialog.value.querySelectorAll("button:not(:disabled),select:not(:disabled)")];
  if (event.shiftKey && document.activeElement === controls[0]) { event.preventDefault(); controls.at(-1)?.focus(); }
  else if (!event.shiftKey && document.activeElement === controls.at(-1)) { event.preventDefault(); controls[0]?.focus(); }
}
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="changelog-backdrop" @mousedown.self="emit('close')" @keydown="onKeydown">
      <section ref="dialog" class="changelog-dialog" role="dialog" aria-modal="true" aria-labelledby="changelog-title" tabindex="-1">
        <header class="changelog-header">
          <h2 id="changelog-title">更新日志</h2>
          <button type="button" class="icon-button" aria-label="关闭更新日志" @click="emit('close')"><X :size="20" /></button>
        </header>
        <div class="changelog-content">
          <label class="changelog-version">版本
            <select v-model="selected" aria-label="选择更新日志版本">
              <option v-for="entry in entries" :key="entry.version" :value="entry.version">v{{ entry.version }}</option>
            </select>
            <time v-if="current?.date">{{ current.date }}</time>
          </label>
          <template v-if="current">
            <section v-for="group in current.groups" :key="group.title" class="changelog-group">
              <h3>{{ { Added: '新功能', Changed: '改进', Fixed: '修复', 'Known Limitations': '已知限制' }[group.title] || group.title }}</h3>
              <ol><li v-for="(item, index) in group.items" :key="index">{{ item }}</li></ol>
            </section>
          </template>
          <p v-else>暂无已发布版本记录。</p>
        </div>
        <footer class="changelog-actions"><button class="button dark" type="button" @click="emit('close')">知道了</button></footer>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.changelog-backdrop{position:fixed;inset:0;z-index:110;display:grid;place-items:center;padding:24px;background:rgba(23,31,42,.48)}
.changelog-dialog{display:flex;flex-direction:column;width:min(760px,100%);max-height:min(840px,calc(100dvh - 48px));overflow:hidden;border-radius:16px;background:var(--surface);box-shadow:0 24px 80px rgba(20,30,42,.25);outline:none}
.changelog-header{display:flex;align-items:center;justify-content:space-between;padding:24px 28px 12px}.changelog-header h2{font-size:24px;font-weight:600}
.changelog-content{flex:1;min-height:0;overflow:auto;overscroll-behavior:contain;padding:12px 28px 24px;color:var(--ink)}
.changelog-version{display:flex;align-items:center;gap:10px;font-weight:600}.changelog-version select{max-width:220px;padding:6px 10px;border:1px solid var(--line);border-radius:8px;background:var(--surface)}.changelog-version time{font-size:13px;color:var(--muted);font-weight:400}
.changelog-group{margin-top:22px}.changelog-group h3{font-size:15px;font-weight:600}.changelog-group ol{margin:12px 0 0;padding-left:24px}.changelog-group li{padding-left:4px;margin:0 0 10px;line-height:1.65;overflow-wrap:anywhere}
.changelog-actions{display:flex;justify-content:flex-end;padding:16px 28px;border-top:1px solid var(--line)}
.changelog-dialog button:focus-visible,.changelog-dialog select:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
</style>
