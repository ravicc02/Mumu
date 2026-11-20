<script setup>
import { computed, ref, watch } from "vue";
import { Camera, Check, LoaderCircle, Save, Trash2 } from "@lucide/vue";
import AvatarCropper from "./AvatarCropper.vue";
import LocalProfileAvatar from "./LocalProfileAvatar.vue";
const props = defineProps({ profile: { type:Object, required:true }, saveProfile: { type:Function, required:true } });
const draft=ref({...props.profile}), saving=ref(false), error=ref(""), message=ref(""), fileInput=ref(null);
const cropOpen=ref(false), cropFile=ref(null);
const dirty=computed(()=>JSON.stringify(draft.value)!==JSON.stringify(props.profile));
watch(()=>props.profile, value=>{draft.value={...value};}, {deep:true});
function pick(event) {
  const file=event.target.files?.[0]; event.target.value="";
  if(!file)return;
  error.value="";message.value="";
  cropFile.value=file;
  cropOpen.value=true;
}
function applyAvatar(avatar) {
  draft.value.avatar=avatar;
  cropOpen.value=false;
  cropFile.value=null;
  error.value="";
}
function closeCropper() {
  cropOpen.value=false;
  cropFile.value=null;
}
async function save() {
  if(saving.value||cropOpen.value)return;
  saving.value=true;error.value="";message.value="";
  try { await props.saveProfile({...draft.value});message.value="资料已保存"; }
  catch(failure){error.value=failure.message;}
  finally{saving.value=false;}
}
</script>
<template>
  <div class="local-account-settings-shell">
    <form class="local-account-settings" @submit.prevent="save">
      <section class="local-profile-summary settings-card">
        <div class="local-profile-identity">
          <span class="profile-avatar-frame">
            <LocalProfileAvatar :profile="draft" :size="88" />
          </span>
          <div class="local-profile-copy">
            <span class="local-profile-kicker">本地账户</span>
            <strong>{{ draft.nickname || '未设置昵称' }}</strong>
          </div>
        </div>
        <div class="local-profile-avatar-actions">
          <button class="button light" type="button" :disabled="cropOpen || saving" aria-label="更换本地头像" @click="fileInput.click()">
            <Camera :size="15" />更换头像
          </button>
          <button v-if="draft.avatar" class="icon-button" type="button" :disabled="cropOpen || saving" title="移除头像" aria-label="移除本地头像" @click="draft.avatar=''">
            <Trash2 :size="16" />
          </button>
          <input ref="fileInput" type="file" accept="image/png,image/jpeg,image/webp" aria-label="选择头像图片" class="profile-file-input" :disabled="cropOpen || saving" @change="pick">
        </div>
      </section>

      <section class="local-profile-editor settings-card">
        <header class="settings-card-heading">
          <span class="settings-card-index">01</span>
          <div class="settings-card-title">
            <h3>显示资料</h3>
          </div>
        </header>
        <div class="settings-card-body padded">
          <div class="local-profile-fields">
            <label class="settings-field">
              <span>昵称</span>
              <input v-model="draft.nickname" autocomplete="nickname" required maxlength="40" :disabled="saving" aria-label="本地昵称">
            </label>
            <label class="settings-field">
              <span>邮箱</span>
              <input v-model="draft.email" type="email" autocomplete="email" maxlength="254" :disabled="saving" placeholder="未设置" aria-label="本地邮箱">
            </label>
          </div>
        </div>
        <footer class="settings-action-bar preferences-save-bar local-profile-save">
          <span class="preferences-feedback" :class="{error}" role="status">
            <span v-if="error || dirty" class="settings-feedback-dot" :class="{ dirty: dirty && !error, error }" aria-hidden="true" />
            <Check v-if="message && !error && !dirty" :size="14" />
            {{ error || (!dirty ? message : '有未保存的更改') }}
          </span>
          <button class="button dark" type="submit" :disabled="!dirty || saving || cropOpen">
            <LoaderCircle v-if="saving" class="spinning" :size="14" />
            <Save v-else :size="14" />保存资料
          </button>
        </footer>
      </section>
    </form>
    <AvatarCropper :open="cropOpen" :file="cropFile" @apply="applyAvatar" @close="closeCropper" />
  </div>
</template>
<style scoped>
.local-account-settings-shell {
  width: 100%;
}

.local-account-settings {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 16px;
  width: 100%;
}

.local-profile-summary {
  display: flex;
  flex-direction: row;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  min-height: 0;
  border-top: 2px solid var(--accent);
  padding: 20px 24px;
  background: var(--ds-surface-muted);
}

.local-profile-identity {
  display: flex;
  align-items: center;
  gap: 18px;
  min-width: 0;
}

.profile-avatar-frame {
  display: grid;
  place-items: center;
  width: 88px;
  height: 88px;
  flex: none;
  border: 1px solid var(--ds-border);
  border-radius: var(--ds-radius-md);
  background: var(--surface);
}

.local-profile-copy {
  display: grid;
  gap: 4px;
}

.local-profile-kicker {
  color: var(--accent);
  font-size: var(--ds-text-xs);
}

.local-profile-copy strong {
  overflow-wrap: anywhere;
  color: var(--ink);
  font-size: var(--ds-text-xl);
  line-height: 1.35;
  font-weight: 500;
}

.local-profile-copy p {
  color: var(--muted);
  font-size: var(--ds-text-sm);
  line-height: 1.65;
}

.local-profile-avatar-actions {
  display: flex;
  align-items: center;
  gap: 6px;
}

.profile-file-input {
  display: none;
}

.local-profile-editor {
  min-width: 0;
}

.local-profile-fields {
  display: grid;
  gap: 18px;
}

.local-profile-fields input {
  height: 42px;
}

.local-profile-save {
  margin-top: 0;
  padding-inline: 20px;
}

.local-profile-save > span {
  min-height: 18px;
  margin-right: auto;
}

@media (max-width: 760px) {
  .local-profile-summary {
    flex-direction: column;
    align-items: flex-start;
  }
}

@media (prefers-reduced-motion: reduce) {
  .spinning {
    animation: none;
  }
}
</style>
