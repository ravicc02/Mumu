<script setup>
import { computed, ref, watch } from "vue";
import { api } from "../api";
const props = defineProps({ account: { type: Object, required: true } });
const failed = ref(false);
const source = computed(() => props.account.avatar_mime ? api.accountAvatarUrl(props.account) : '');
watch(source, () => { failed.value = false; });
</script>
<template><span class="account-avatar"><img v-if="source && !failed" :src="source" :alt="account.name + ' 的头像'" draggable="false" @error="failed = true"><span v-else>{{ (account.name || '?').slice(0,1).toUpperCase() }}</span><slot /></span></template>
<style scoped>.account-avatar { overflow: hidden; }.account-avatar img { width: 100%; height: 100%; object-fit: cover; display: block; }</style>
