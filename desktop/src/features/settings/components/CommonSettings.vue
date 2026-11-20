<script setup>
import ToggleSwitch from "../../../shared/controls/ToggleSwitch.vue";
import { onMounted, ref } from "vue";
import { LoaderCircle } from "@lucide/vue";

const props=defineProps({
  autoCheck:{type:Boolean,default:true},
  setAutoCheck:{type:Function,required:true},
});
const autoLaunchSupported=ref(Boolean(window.desktopApi?.getAutoLaunch));
const autoLaunch=ref(false), loading=ref(true), pending=ref(""), error=ref("");

async function loadAutoLaunch() {
  if (!window.desktopApi?.getAutoLaunch) {
    autoLaunchSupported.value=false;
    loading.value=false;
    return;
  }
  loading.value=true;
  error.value="";
  try {
    const state=await window.desktopApi.getAutoLaunch();
    autoLaunchSupported.value=Boolean(state?.supported);
    autoLaunch.value=Boolean(state?.enabled);
  } catch(failure) {
    autoLaunchSupported.value=false;
    error.value=failure.message;
  } finally {
    loading.value=false;
  }
}

async function toggleAutoLaunch(event) {
  const previous=autoLaunch.value;
  const next=Boolean(event.target.checked);
  pending.value="auto-launch";
  error.value="";
  try {
    const state=await window.desktopApi.setAutoLaunch(next);
    autoLaunchSupported.value=Boolean(state?.supported);
    autoLaunch.value=Boolean(state?.enabled);
  } catch(failure) {
    autoLaunch.value=previous;
    event.target.checked=previous;
    error.value=failure.message;
  } finally {
    pending.value="";
  }
}

async function toggleAutoCheck(event) {
  const previous=props.autoCheck;
  pending.value="auto-check";
  error.value="";
  try {
    await props.setAutoCheck(Boolean(event.target.checked));
  } catch(failure) {
    event.target.checked=previous;
    error.value=failure.message;
  } finally {
    pending.value="";
  }
}

onMounted(loadAutoLaunch);
</script>

<template>
  <div class="common-settings">
    <section class="settings-card">
      <header class="settings-card-heading">
        <span class="settings-card-index">01</span>
        <div class="settings-card-title">
          <h3>启动</h3>
        </div>
      </header>
      <div class="settings-card-body">
        <label class="preference-row">
          <span>
            <strong>开机自启</strong>
            <small v-if="!loading && !autoLaunchSupported">安装版可用</small>
          </span>
          <span class="common-switch">
            <LoaderCircle v-if="loading || pending === 'auto-launch'" class="spinning" :size="14" />
            <ToggleSwitch
              role="switch"
              aria-label="开机自启"
              :checked="autoLaunch"
              :disabled="loading || !autoLaunchSupported || Boolean(pending)"
              @change="toggleAutoLaunch" />
          </span>
        </label>
        <label class="preference-row">
          <span>
            <strong>启动时自动检查更新</strong>
          </span>
          <span class="common-switch">
            <LoaderCircle v-if="pending === 'auto-check'" class="spinning" :size="14" />
            <ToggleSwitch
              role="switch"
              aria-label="启动时自动检查更新"
              :checked="autoCheck"
              :disabled="Boolean(pending)"
              @change="toggleAutoCheck" />
          </span>
        </label>
      </div>
    </section>
    <p v-if="error" class="common-settings-error" role="alert">{{ error }}</p>
  </div>
</template>

<style scoped>
.common-settings {
  display: grid;
  gap: var(--module-gap, 24px);
  width: 100%;
}

.common-switch {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.common-settings-error {
  margin: 0;
  border: 1px solid rgba(182,65,59,.18);
  border-radius: 8px;
  background: var(--red-soft);
  padding: 10px 12px;
  color: var(--red);
  font-size: 12px;
  line-height: 1.6;
}

.spinning {
  animation: icon-spin 1s linear infinite;
}

@media (prefers-reduced-motion: reduce) {
  .spinning {
    animation: none;
  }
}
</style>
