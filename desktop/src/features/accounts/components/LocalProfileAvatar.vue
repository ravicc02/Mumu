<script setup>
import { UserRound } from "@lucide/vue";
defineProps({ profile: { type: Object, required: true }, size: { type: Number, default: 34 } });
</script>
<template><span class="local-profile-avatar" :style="{width:size+'px',height:size+'px'}"><img v-if="profile.avatar" :src="profile.avatar" :alt="profile.nickname + ' 的头像'" draggable="false"><UserRound v-else :size="Math.round(size*.52)" :stroke-width="1.6" aria-hidden="true" /></span></template>
<style scoped>.local-profile-avatar { display:inline-grid;place-items:center;flex:none;overflow:hidden;border-radius:50%;border:1px solid var(--line-strong);background:var(--accent-soft);color:var(--accent); }.local-profile-avatar img { display:block;width:100%;height:100%;object-fit:cover; }</style>
