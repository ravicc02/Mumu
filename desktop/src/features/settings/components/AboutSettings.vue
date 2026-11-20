<script setup>
import { ref, watch } from "vue";
import { ArrowUpRight, Check, FileText, FolderOpen, LoaderCircle, RefreshCw, Scale } from "@lucide/vue";
import ChangelogDialog from "./ChangelogDialog.vue";
import { appInfo, displayVersion } from "../../../app/lib/product.js";
import ToggleSwitch from "../../../shared/controls/ToggleSwitch.vue";

const props = defineProps({
  update: { type: Object, required: true },
  visible: { type: Boolean, default: false },
  autoCheck: { type: Boolean, default: false },
  setAutoCheck: { type: Function, required: true },
  checkUpdates: { type: Function, required: true },
  openExternal: { type: Function, required: true },
  openLogs: { type: Function, required: true },
  close: { type: Function, required: true },
});

const error = ref("");
const changelogOpen = ref(false);
watch(() => props.visible, visible => { if (!visible) changelogOpen.value = false; });
const year = new Date().getFullYear();
const statusText = {
  available: "发现可用更新",
  current: "当前已是最新版本",
  checking: "正在检查更新",
  error: "检查更新失败",
};

async function openLink(url) {
  error.value = "";
  try {
    await props.openExternal(url);
  } catch (failure) {
    error.value = failure.message;
  }
}

function toggleAutoCheck(event) {
  error.value = "";
  try {
    props.setAutoCheck(event.target.checked);
  } catch (failure) {
    error.value = failure.message;
  }
}
</script>

<template>
  <div class="about-settings">
    <div class="about-body">
      <header class="about-hero">
        <span class="about-mark"><img src="/brand/mumu.png" width="72" height="72" alt="Mumu" /></span>
        <h3>{{ appInfo.name }}</h3>
        <p class="about-version">版本 {{ displayVersion }}</p>
        <button class="about-home-link" type="button" @click="openLink(appInfo.repository)">
          访问项目主页<ArrowUpRight :size="13" />
        </button>
      </header>

      <section class="about-update-card">
        <div class="about-update-head">
          <div class="about-update-copy">
            <strong>自动检查更新</strong>
            <p>启动时自动检查新版本</p>
          </div>
          <ToggleSwitch
            role="switch"
            aria-label="启动时自动检查更新"
            :checked="autoCheck"
            @change="toggleAutoCheck" />
        </div>

        <button class="button dark about-check-button" type="button" :disabled="update.status === 'checking'" @click="checkUpdates">
          <LoaderCircle v-if="update.status === 'checking'" class="spinning" :size="15" />
          <RefreshCw v-else :size="15" />
          {{ update.status === 'checking' ? '正在检查' : '检查更新' }}
        </button>

        <p class="about-update-status" :class="update.status" role="status">
          <Check v-if="update.status === 'current'" :size="13" />
          <span>
            {{ statusText[update.status] || '尚未检查已发布版本' }}
            <template v-if="update.status === 'available' && update.version"> · {{ update.version }}</template>
            <template v-else-if="update.message"> · {{ update.message }}</template>
          </span>
          <button v-if="update.status === 'available'" class="about-inline-link" type="button" @click="openLink(update.url)">
            查看新版本<ArrowUpRight :size="12" />
          </button>
        </p>
      </section>

      <nav class="about-links" aria-label="关于页面操作">
        <button type="button" @click="changelogOpen = true">
          <FileText :size="15" />查看更新日志
        </button>
        <button type="button" @click="openLogs">
          <FolderOpen :size="15" />打开日志目录
        </button>
      </nav>

      <p class="about-copyright">© {{ year }} {{ appInfo.name }}. All rights reserved.</p>
      <button class="about-license" type="button" @click="openLink(appInfo.repository + '/blob/main/LICENSE')"><Scale :size="13" />MIT</button>
    </div>

    <footer class="about-actions">
      <button class="button dark" type="button" @click="close">完成</button>
    </footer>

    <p v-if="error" class="about-error validation-error" role="alert">{{ error }}</p>
    <ChangelogDialog :open="changelogOpen" @close="changelogOpen = false" />
  </div>
</template>

<style scoped>
/* finesse · register=product · shell=settings-about · layout=centered-colophon */
.about-settings {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  width: 100%;
}

.about-body {
  display: flex;
  flex: 1;
  flex-direction: column;
  align-items: center;
  padding: 20px 0 0;
}

.about-hero {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
}

.about-mark {
  display: grid;
  place-items: center;
  width: 88px;
  height: 88px;
  border: 1px solid var(--ds-border);
  border-radius: var(--ds-radius-lg);
  background: var(--surface);
  box-shadow: var(--ds-elevation-100);
}

.about-hero h3 {
  margin: 16px 0 0;
  color: var(--ink);
  font-size: var(--ds-text-3xl);
  line-height: 1.15;
  font-weight: 500;
  letter-spacing: -.01em;
}

.about-version {
  margin: 8px 0 0;
  color: var(--muted);
  font-size: var(--ds-text-base);
  font-variant-numeric: tabular-nums;
}

.about-home-link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-top: 12px;
  border: 0;
  background: transparent;
  padding: 0;
  color: var(--accent);
  font-size: var(--ds-text-base);
}

.about-update-card {
  width: min(560px, 100%);
  margin-top: 34px;
  border: 1px solid var(--line);
  border-radius: var(--ds-radius-md);
  background: var(--surface);
  padding: 18px 20px 16px;
}

.about-update-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
}

.about-update-copy {
  min-width: 0;
}

.about-update-copy strong {
  display: block;
  color: var(--ink);
  font-size: var(--ds-text-base);
  font-weight: 500;
}

.about-update-copy p {
  margin: 4px 0 0;
  color: var(--muted);
  font-size: var(--ds-text-sm);
  line-height: 1.5;
}

.about-check-button {
  justify-content: center;
  width: 100%;
  margin-top: 16px;
}

.about-update-status {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-wrap: wrap;
  gap: 4px;
  margin: 12px 0 0;
  color: var(--muted);
  font-size: var(--ds-text-sm);
  line-height: 1.5;
  text-align: center;
}

.about-update-status.current {
  color: var(--green);
}

.about-update-status.error {
  color: var(--red);
}

.about-inline-link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: 0;
  background: transparent;
  padding: 0;
  color: var(--accent);
  font-size: var(--ds-text-sm);
}

.about-links {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-wrap: wrap;
  gap: 10px 30px;
  margin-top: 28px;
}

.about-links button {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  border: 0;
  background: transparent;
  padding: 2px 0;
  color: var(--muted);
  font-size: var(--ds-text-sm);
}

.about-copyright {
  margin: 46px 0 0;
  color: var(--subtle);
  font-size: var(--ds-text-sm);
  text-align: center;
}

.about-license{display:inline-flex;align-items:center;gap:4px;margin-top:8px;border:0;background:transparent;color:var(--subtle);font-size: var(--ds-text-xs)}
.about-license:hover{color:var(--ink)}
.about-actions {
  display: flex;
  justify-content: flex-end;
  margin-top: auto;
  padding-top: 28px;
}

.about-error {
  margin-top: 12px;
  border: 1px solid color-mix(in srgb, var(--ds-danger) 18%, transparent);
  border-radius: var(--ds-radius-sm);
  background: var(--red-soft);
  padding: 10px 12px;
  font-size: var(--ds-text-sm);
}

@media (hover: hover) {
  .about-home-link:hover,
  .about-inline-link:hover,
  .about-links button:hover {
    color: var(--ink);
  }
}

@media (max-width: 680px) {
  .about-update-card {
    padding: 16px 16px 14px;
  }

  .about-links {
    gap: 8px 18px;
  }

  .about-copyright {
    margin-top: 32px;
  }
}
</style>
