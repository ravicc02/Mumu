<script setup>
import { computed, onMounted, ref } from 'vue';
import { Check, CircleAlert, Globe2, LoaderCircle, Save, Server } from '@lucide/vue';
import { api } from '../../../app/api.js';

const baseUrl = ref('');
const secret = ref('');
const status = ref({});
const loading = ref(true);
const busy = ref('');
const message = ref('');
const error = ref('');
const dirty = computed(() => baseUrl.value.trim() !== (status.value.base_url || '') || Boolean(secret.value.trim()));

async function load() {
  loading.value = true; error.value = '';
  try {
    status.value = await api.attendanceRelay();
    baseUrl.value = status.value.base_url || '';
  } catch (cause) { error.value = cause.message; }
  finally { loading.value = false; }
}

function payload() {
  return { base_url: baseUrl.value.trim(), admin_secret: secret.value.trim() };
}

async function test() {
  busy.value = 'test'; error.value = ''; message.value = '';
  try {
    await api.testAttendanceRelay(payload());
    message.value = '连接成功';
  } catch (cause) { error.value = cause.message; }
  finally { busy.value = ''; }
}

async function save() {
  busy.value = 'save'; error.value = ''; message.value = '';
  try {
    status.value = { ...status.value, ...(await api.saveAttendanceRelay(payload())) };
    baseUrl.value = status.value.base_url || baseUrl.value.trim();
    secret.value = '';
    message.value = '域名配置已保存';
  } catch (cause) { error.value = cause.message; }
  finally { busy.value = ''; }
}

onMounted(load);
</script>

<template>
  <section class="preference-section">
    <div class="preference-section-heading"><h3><Globe2 :size="15" />签到域名</h3><span class="count">{{ status.configured ? '已连接' : '未配置' }}</span></div>
    <div v-if="loading" class="preferences-loading"><LoaderCircle :size="17" class="spinning" />正在读取域名配置</div>
    <template v-else>
      <label class="preference-row"><span><strong>中继服务地址</strong><small>填写自建扫码服务的 HTTPS 域名，例如 https://sign.example.com</small></span><input v-model="baseUrl" type="url" placeholder="https://sign.example.com" autocomplete="url"></label>
      <label class="preference-row"><span><strong>管理密钥</strong><small>{{ status.admin_secret_configured ? '已保存，留空保持不变' : '首次配置必须填写服务端管理密钥' }}</small></span><input v-model="secret" type="password" placeholder="至少 24 位" autocomplete="new-password"></label>
      <p v-if="error" class="preferences-feedback error"><CircleAlert :size="14" />{{ error }}</p>
      <p v-if="message" class="preferences-feedback"><Check :size="14" />{{ message }}</p>
      <div class="inline-form">
        <span></span>
        <div class="attendance-domain-actions">
          <button class="button light" type="button" :disabled="Boolean(busy) || !baseUrl.trim()" @click="test"><LoaderCircle v-if="busy === 'test'" :size="14" class="spinning" /><Server v-else :size="14" />测试连接</button>
          <button class="button dark" type="button" :disabled="Boolean(busy) || !dirty" @click="save"><LoaderCircle v-if="busy === 'save'" :size="14" class="spinning" /><Save v-else :size="14" />保存配置</button>
        </div>
      </div>
    </template>
  </section>

  <section class="preference-section">
    <div class="preference-section-heading"><h3>域名接入</h3><span class="count">{{ status.multi_domain_ready ? '共享中继' : '自建中继' }}</span></div>
    <template v-if="status.multi_domain_ready">
      <p class="preference-consequence">共享中继需要 DNS 验证和服务器自动签发证书。添加下面的记录后，再把域名填入上方。</p>
      <div class="preference-row path-preference"><span><strong>CNAME</strong><code>{{ status.cname_name }} → {{ status.cname_target }}</code></span></div>
      <div class="preference-row path-preference"><span><strong>TXT</strong><code>{{ status.txt_name }} = {{ status.txt_value }}</code></span></div>
    </template>
    <p v-else class="preference-consequence">当前使用自建中继模式：把自己的域名解析到中继服务器，配置 HTTPS 反向代理，然后填写服务地址和管理密钥。共享服务器的 CNAME/TXT 自动接入需要服务端部署多租户证书与 DNS 验证控制面，未配置时不会伪造验证结果。</p>
  </section>
</template>
