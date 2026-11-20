<script setup>
import ToggleSwitch from "../../../shared/controls/ToggleSwitch.vue";
import { onMounted, ref } from "vue";
import { LoaderCircle, Monitor, Moon, Sun } from "@lucide/vue";
import { applyAppearance, loadAppearance, saveAppearance } from "../../../app/lib/appearance.js";

const themeModes = [{ id: "system", label: "跟随系统", icon: Monitor }, { id: "light", label: "亮色", icon: Sun }, { id: "dark", label: "暗色", icon: Moon }];
const appearance = ref(loadAppearance());
function commitAppearance(next) {
  appearance.value = next;
  saveAppearance(next);
  applyAppearance(next);
}
function setTheme(theme) { commitAppearance({ ...appearance.value, theme }); }
function setReduceMotion(event) { commitAppearance({ ...appearance.value, reduceMotion: Boolean(event.target.checked) }); }

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
          <h3>外观</h3>
        </div>
      </header>
      <div class="settings-card-body">
        <div class="preference-row">
          <span>
            <strong>主题</strong>
          </span>
          <div class="appearance-choice" role="radiogroup" aria-label="主题">
            <button
              v-for="mode in themeModes"
              :key="mode.id"
              type="button"
              role="radio"
              :aria-checked="appearance.theme === mode.id"
              :class="{ active: appearance.theme === mode.id }"
              @click="setTheme(mode.id)">
              <component :is="mode.icon" :size="14" />{{ mode.label }}
            </button>
          </div>
        </div>
        <label class="preference-row">
          <span>
            <strong>减少动效</strong>
          </span>
          <ToggleSwitch
            role="switch"
            aria-label="减少动效"
            :checked="appearance.reduceMotion"
            @change="setReduceMotion" />
        </label>
      </div>
    </section>
    <section class="settings-card">
      <header class="settings-card-heading">
        <span class="settings-card-index">02</span>
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
  gap: 12px;
  width: 100%;
}

.appearance-choice {
  display: inline-flex;
  gap: var(--ds-space-1);
  padding: var(--ds-space-1);
  border-radius: var(--ds-radius-pill);
  background: var(--ds-surface-300);
}

.appearance-choice button {
  display: inline-flex;
  align-items: center;
  gap: var(--ds-space-1-5);
  height: 28px;
  padding: 0 var(--ds-space-3);
  border: 0;
  border-radius: var(--ds-radius-pill);
  background: transparent;
  color: var(--ds-text-secondary);
  font-size: var(--ds-text-sm);
  font-weight: var(--ds-weight-control);
}

.appearance-choice button:hover:not(.active) { background: var(--ds-on-surface); }
.appearance-choice button.active { background: var(--ds-surface-100); color: var(--ds-selected-ink); box-shadow: var(--ds-elevation-100); }

.common-switch {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.common-settings-error {
  margin: 0;
  border: 1px solid color-mix(in srgb, var(--ds-danger) 18%, transparent);
  border-radius: var(--ds-radius-sm);
  background: var(--red-soft);
  padding: 10px 12px;
  color: var(--red);
  font-size: var(--ds-text-sm);
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
