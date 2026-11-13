<script setup>
import { computed } from "vue";
import { BookOpen, ClipboardList, ClipboardCheck } from "@lucide/vue";
import { businessTypes, supportsBusiness } from "../lib/catalog.js";
const props = defineProps({ platform: { type: [String, Object], required: true } });
const supported = computed(() => businessTypes.filter(item => supportsBusiness(props.platform, item.id)));
</script>

<template>
  <span class="platform-capabilities" :aria-label="'支持：' + supported.map(item => item.label).join('、')">
    <span v-for="item in supported" :key="item.id"><ClipboardList v-if="item.id === 'assignments'" :size="11" aria-hidden="true" /><ClipboardCheck v-else-if="item.id === 'attendance'" :size="11" aria-hidden="true" /><BookOpen v-else :size="11" aria-hidden="true" />{{ item.label }}</span>
  </span>
</template>

<style scoped>
.platform-capabilities { display: inline-flex; flex-wrap: wrap; align-items: center; gap: 9px; color: var(--muted); font-size: 10px; line-height: 1.5; }
.platform-capabilities > span { display: inline-flex; align-items: center; gap: 4px; white-space: nowrap; }
</style>
