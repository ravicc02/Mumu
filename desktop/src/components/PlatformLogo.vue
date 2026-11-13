<script setup>
import { computed } from "vue";
import { Globe2 } from "@lucide/vue";
import { platformById } from "../lib/platforms";
const props = defineProps({ platform: { type: String, default: "chaoxing" }, size: { type: Number, default: 32 } });
const asset = computed(() => (props.platform === "weixin" ? "./platforms/wechat.svg" : platformById[props.platform] ? "./platforms/" + platformById[props.platform].logo : ""));
const label = computed(() => (props.platform === "weixin" ? "微信" : platformById[props.platform]?.label || props.platform));
</script>
<template>
  <span class="platform-logo" :class="platform" :style="{ width: `${size}px`, height: `${size}px` }">
    <img v-if="asset" :src="asset" :alt="`${label} Logo`" draggable="false" />
    <Globe2 v-else :size="Math.round(size * .65)" :aria-label="label" />
  </span>
</template>
