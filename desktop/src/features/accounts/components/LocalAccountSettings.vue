<script setup>
import { computed, ref, watch } from "vue";
import { Camera, Check, LoaderCircle, Save, Trash2 } from "@lucide/vue";
import { avatarFromFile } from "../lib/local-profile.js";
import LocalProfileAvatar from "./LocalProfileAvatar.vue";
const props = defineProps({ profile: { type:Object, required:true }, saveProfile: { type:Function, required:true } });
const draft=ref({...props.profile}), picking=ref(false), saving=ref(false), error=ref(""), message=ref(""), fileInput=ref(null);
const dirty=computed(()=>JSON.stringify(draft.value)!==JSON.stringify(props.profile));
watch(()=>props.profile, value=>{draft.value={...value};}, {deep:true});
async function pick(event) {
  const file=event.target.files?.[0]; event.target.value="";
  if(!file)return;
  picking.value=true;error.value="";message.value="";
  try { draft.value.avatar=await avatarFromFile(file); }
  catch(failure){error.value=failure.message;}
  finally{picking.value=false;}
}
async function save() {
  if(saving.value||picking.value)return;
  saving.value=true;error.value="";message.value="";
  try { await props.saveProfile({...draft.value});message.value="资料已保存"; }
  catch(failure){error.value=failure.message;}
  finally{saving.value=false;}
}
</script>
<template><form class="local-account-settings" @submit.prevent="save"><section class="local-avatar-row"><div><h3>头像</h3><button class="profile-avatar-picker" type="button" :disabled="picking||saving" aria-label="更换本地头像" @click="fileInput.click()"><LocalProfileAvatar :profile="draft" :size="76" /><span class="profile-avatar-action"><LoaderCircle v-if="picking" class="spinning" :size="15" /><Camera v-else :size="15" />{{picking?'正在处理':'更换头像'}}</span></button><input ref="fileInput" type="file" accept="image/png,image/jpeg,image/webp" aria-label="选择头像图片" class="profile-file-input" :disabled="picking||saving" @change="pick"></div><button v-if="draft.avatar" class="icon-button" type="button" :disabled="picking||saving" title="移除头像" aria-label="移除本地头像" @click="draft.avatar='' "><Trash2 :size="16" /></button></section><label class="local-profile-field"><span>昵称</span><input v-model="draft.nickname" autocomplete="nickname" required maxlength="40" :disabled="saving" aria-label="本地昵称"></label><label class="local-profile-field"><span>邮箱</span><input v-model="draft.email" type="email" autocomplete="email" maxlength="254" :disabled="saving" placeholder="未设置" aria-label="本地邮箱"></label><footer class="preferences-save-bar local-profile-save"><span class="preferences-feedback" :class="{error}" role="status"><Check v-if="message&&!error&&!dirty" :size="14" />{{error||(!dirty?message:'')}}</span><button class="button dark" type="submit" :disabled="!dirty||saving||picking"><LoaderCircle v-if="saving" class="spinning" :size="14" /><Save v-else :size="14" />保存资料</button></footer></form></template>
<style scoped>
.local-account-settings{max-width:780px;}.local-avatar-row{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:8px 0 26px;border-bottom:1px solid var(--line);}.local-avatar-row h3{font-size:13px;font-weight:600;margin-bottom:18px;}.profile-avatar-picker{display:flex;align-items:center;gap:20px;padding:0;background:transparent;border:0;color:var(--accent);text-align:left;}.profile-avatar-action{display:flex;align-items:center;gap:7px;font-size:12px;}.profile-file-input{display:none;}.local-profile-field{display:grid;gap:14px;padding:24px 0;border-bottom:1px solid var(--line);font-size:13px;font-weight:600;}.local-profile-field input{height:38px;width:min(100%,440px);min-width:0;border:1px solid var(--line-strong);border-radius:5px;padding:0 11px;font:inherit;font-size:13px;font-weight:400;color:var(--ink);background:var(--surface);}.local-profile-field input:focus-visible,.profile-avatar-picker:focus-visible{outline:2px solid var(--accent);outline-offset:3px;}.profile-avatar-picker:disabled{opacity:.5;cursor:not-allowed;}.local-profile-save{justify-content:flex-end;}.local-profile-save>span{margin-right:auto;min-height:18px;}.profile-avatar-picker:active{transform:translateY(1px);}@media(hover:hover){.profile-avatar-picker:hover{color:var(--accent-hover);}}@media(prefers-reduced-motion:reduce){.profile-avatar-picker .spinning{animation:none;}}
</style>
