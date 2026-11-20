<script setup>
import { computed, onMounted, ref } from 'vue';
import { Check, CircleAlert, Cloud, LoaderCircle, Save, Server } from '@lucide/vue';
import { api } from '../../../app/api.js';

const mode = ref('official');
const baseUrl = ref('');
const secret = ref('');
const status = ref({});
const loading = ref(true);
const busy = ref('');
const message = ref('');
const error = ref('');
const customMode = computed(() => mode.value === 'custom');
const savedCustomMode = computed(() => status.value.mode === 'custom');
const dirty = computed(() => {
  const savedMode = status.value.mode === 'custom' ? 'custom' : 'official';
  if (mode.value !== savedMode) return true;
  return customMode.value && (baseUrl.value.trim() !== (status.value.base_url || '') || Boolean(secret.value.trim()));
});

async function load() {
  loading.value = true;
  error.value = '';
  try {
    status.value = await api.attendanceRelay();
    mode.value = status.value.mode === 'custom' ? 'custom' : 'official';
    baseUrl.value = mode.value === 'custom' ? (status.value.base_url || '') : '';
    secret.value = '';
  } catch (cause) {
    error.value = cause.message;
  } finally {
    loading.value = false;
  }
}

function chooseMode(value) {
  if (mode.value === value) return;
  mode.value = value;
  secret.value = '';
  message.value = '';
  error.value = '';
}

function payload() {
  if (mode.value === 'official') {
    return { mode: 'official', base_url: '', admin_secret: '' };
  }
  return {
    mode: 'custom',
    base_url: baseUrl.value.trim(),
    admin_secret: secret.value.trim(),
  };
}

async function test() {
  busy.value = 'test';
  error.value = '';
  message.value = '';
  try {
    await api.testAttendanceRelay(payload());
    message.value = '连接成功';
  } catch (cause) {
    error.value = cause.message;
  } finally {
    busy.value = '';
  }
}

async function save() {
  busy.value = 'save';
  error.value = '';
  message.value = '';
  try {
    status.value = { ...status.value, ...(await api.saveAttendanceRelay(payload())) };
    mode.value = status.value.mode === 'custom' ? 'custom' : 'official';
    baseUrl.value = mode.value === 'custom' ? (status.value.base_url || '') : '';
    secret.value = '';
    message.value = '中继服务已保存';
  } catch (cause) {
    error.value = cause.message;
  } finally {
    busy.value = '';
  }
}

onMounted(load);
</script>

<template>
  <div class="attendance-domain-settings settings-section-stack">
    <section class="settings-card">
      <header class="settings-card-heading">
        <div class="settings-card-title">
          <h3>中继服务</h3>
        </div>
        <div class="settings-card-heading-actions">
          <span class="settings-state" :class="{ ready: status.configured }">{{ dirty ? '未保存' : status.configured ? '已配置' : '未配置' }}</span>
        </div>
      </header>
      <div class="settings-card-body padded">
        <div v-if="loading" class="settings-loading">
          <LoaderCircle :size="18" class="spinning" />正在读取中继配置
        </div>
        <template v-else>
          <div class="attendance-relay-mode" role="radiogroup" aria-label="中继服务来源">
            <button
              type="button"
              role="radio"
              :aria-checked="mode === 'official'"
              :class="{ active: mode === 'official' }"
              @click="chooseMode('official')"
            >
              <Cloud :size="15" />官方中继服务
            </button>
            <button
              type="button"
              role="radio"
              :aria-checked="mode === 'custom'"
              :class="{ active: mode === 'custom' }"
              @click="chooseMode('custom')"
            >
              <Server :size="15" />自定义中继服务
            </button>
          </div>

          <div v-if="customMode" class="attendance-form-grid">
            <label class="settings-field attendance-address-field">
              <span>中继服务地址</span>
              <input v-model="baseUrl" type="url" placeholder="https://sign.example.com" autocomplete="url" aria-label="中继服务地址">
            </label>
            <label class="settings-field">
              <span>管理密钥</span>
              <input v-model="secret" type="password" :placeholder="savedCustomMode && status.admin_secret_configured ? '已保存，留空保持不变' : '至少 24 位'" autocomplete="new-password" aria-label="管理密钥">
            </label>
          </div>

          <p v-if="error" class="attendance-feedback error" role="alert"><CircleAlert :size="14" />{{ error }}</p>
          <p v-if="message" class="attendance-feedback" role="status"><Check :size="14" />{{ message }}</p>
          <footer class="attendance-card-actions">
            <span v-if="customMode">{{ savedCustomMode && status.admin_secret_configured ? '管理密钥已保存' : '首次使用需要填写管理密钥' }}</span>
            <div>
              <button class="button light" type="button" :disabled="Boolean(busy) || (customMode && !baseUrl.trim())" @click="test">
                <LoaderCircle v-if="busy === 'test'" :size="14" class="spinning" />
                <Server v-else :size="14" />测试连接
              </button>
              <button class="button dark" type="button" :disabled="Boolean(busy) || !dirty || (customMode && !baseUrl.trim())" @click="save">
                <LoaderCircle v-if="busy === 'save'" :size="14" class="spinning" />
                <Save v-else :size="14" />保存配置
              </button>
            </div>
          </footer>
        </template>
      </div>
    </section>
  </div>
</template>

<style scoped>
.attendance-domain-settings {
  width: 100%;
}

.attendance-relay-mode {
  display: inline-flex;
  gap: 4px;
  padding: 4px;
  border: 1px solid var(--line);
  border-radius: var(--ds-radius-sm);
  background: var(--surface-muted);
}

.attendance-relay-mode button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 34px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  padding: 0 12px;
  color: var(--muted);
  font-size: var(--ds-text-sm);
  cursor: pointer;
}

.attendance-relay-mode button.active {
  background: var(--surface);
  color: var(--ink);
  box-shadow: 0 1px 3px color-mix(in srgb, var(--ink) 12%, transparent);
}

.attendance-form-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) minmax(240px, .65fr);
  gap: 20px;
  margin-top: 18px;
}

.attendance-feedback {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 14px;
  color: var(--green);
  font-size: var(--ds-text-sm);
}

.attendance-feedback.error {
  color: var(--red);
}

.attendance-card-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  margin-top: 20px;
  border-top: 1px solid var(--line);
  padding-top: 16px;
}

.attendance-card-actions > span {
  color: var(--muted);
  font-size: var(--ds-text-sm);
}

.attendance-card-actions > div {
  display: flex;
  gap: 8px;
  margin-left: auto;
}

@media (max-width: 760px) {
  .attendance-relay-mode {
    display: grid;
    grid-template-columns: 1fr 1fr;
    width: 100%;
  }

  .attendance-form-grid {
    grid-template-columns: 1fr;
  }

  .attendance-card-actions {
    align-items: flex-start;
    flex-direction: column;
  }

  .attendance-card-actions > div {
    width: 100%;
    margin-left: 0;
  }
}
</style>
