<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue';
import { ArrowLeft, ArrowRight, Check, CircleAlert, Copy, Download, LoaderCircle, LogIn, RefreshCw, X } from '@lucide/vue';
import BrandOrbit from './BrandOrbit.vue';
import ModelConfiguration from './ModelConfiguration.vue';
import PlatformLogo from './PlatformLogo.vue';
import AccountAvatar from './AccountAvatar.vue';
import { api } from '../api';
import { platformCatalog, businessTypes } from '../lib/platforms';
import { createAccountAuthorization } from '../lib/account-authorization';
import { completeOnboarding, environmentReady } from '../lib/onboarding';

const props = defineProps({ scope: String, modelConfigured: Boolean, canClose: Boolean, accounts: { type: Array, default: () => [] } });
const emit = defineEmits(['complete', 'ready', 'model-saved', 'authorized', 'close']);
const dialog = ref(null), step = ref(0), state = ref({ status: 'checking', items: [], backendReady: false });
const busy = ref(false), error = ref(''), copied = ref(''), configured = ref(props.modelConfigured), platform = ref('chaoxing');
const modelChoice = ref(props.modelConfigured ? 'configured' : 'skipped'), platformChoice = ref(props.accounts.some(account => account.status === 'ready') ? 'authorized' : 'skipped');
const auth = ref({ status: 'idle', account: null }), finishing = ref(false);
const native = Boolean(window.desktopApi?.checkDependencies);
const checking = computed(() => busy.value || ['checking', 'installing', 'starting'].includes(state.value.status));
const ready = computed(() => environmentReady(state.value));
const installable = computed(() => state.value.items.some(item => item.status === 'missing' && item.installable));
const accountList = ref([...props.accounts]);
const currentPlatform = computed(() => platformCatalog.find(item => item.name === platform.value));
const pendingAuth = computed(() => ['opening', 'waiting', 'verifying'].includes(auth.value.status));
let disposed = false, timer, generation = 0, previousFocus;
watch(() => props.modelConfigured, value => { if (value) { configured.value = true; modelChoice.value = 'configured'; } });
const flow = createAccountAuthorization(api, {
  changed: value => { if (!disposed) auth.value = value; },
  completed: async (account, records) => { if (!disposed) { accountList.value = records; platformChoice.value = 'authorized'; emit('authorized', records); } }
});
async function refreshState(token) {
  if (!native || disposed || token !== generation) return;
  try { const value = await window.desktopApi.bootstrapState(); if (!disposed && token === generation) state.value = value; } catch { /* The command reports its final error. */ }
  if (!disposed && token === generation && busy.value) timer = setTimeout(() => refreshState(token), 700);
}
async function check(install = false) {
  if (busy.value) return;
  busy.value = true; error.value = ''; const token = ++generation;
  try {
    if (native) {
      void refreshState(token);
      state.value = await (install ? window.desktopApi.installDependencies() : window.desktopApi.checkDependencies());
    } else state.value = await api.setupEnvironment();
    if (!disposed && ready.value) emit('ready');
  } catch (failure) {
    state.value = { ...state.value, status: 'error', backendReady: false };
    error.value = failure.message === 'Not Found' ? '请在当前任务结束后重启本地服务，再检测初始化环境。' : failure.message;
  } finally { if (!disposed) busy.value = false; clearTimeout(timer); }
}
async function copy(command, id) {
  try { await navigator.clipboard.writeText(command); copied.value = id; }
  catch { error.value = '无法复制，请选中命令后手动复制。'; }
}
function modelSaved() { configured.value = true; modelChoice.value = 'configured'; emit('model-saved'); }
async function authorize() {
  if (pendingAuth.value) return;
  await flow.start(platform.value);
}
async function next(skip = false) {
  error.value = '';
  if (step.value === 0 && !ready.value) return;
  if (step.value === 1) modelChoice.value = skip ? 'skipped' : 'configured';
  if (step.value < 2) { step.value++; await nextTick(); dialog.value?.focus(); return; }
  finishing.value = true;
  try {
    const info = await api.settings();
    completeOnboarding(info.data_root || props.scope || state.value.scope,
      { model: modelChoice.value, platform: skip ? 'skipped' : platformChoice.value });
    emit('complete');
  } catch (failure) { error.value = failure.message; }
  finally { finishing.value = false; }
}
function trap(event) {
  const nodes = [...dialog.value.querySelectorAll('button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href]')].filter(node => node.getClientRects().length);
  const first = nodes[0], last = nodes.at(-1);
  if (event.shiftKey && [dialog.value, first].includes(document.activeElement)) { event.preventDefault(); last?.focus(); }
  else if (!event.shiftKey && [dialog.value, last].includes(document.activeElement)) { event.preventDefault(); first?.focus(); }
}
onMounted(() => { previousFocus = document.activeElement; dialog.value?.focus(); void check(); });
onUnmounted(() => { disposed = true; generation++; clearTimeout(timer); flow.dispose(); if (previousFocus?.isConnected) previousFocus.focus(); });
</script>

<template>
  <Teleport to="body"><div class="startup-backdrop"><section ref="dialog" class="startup-experience" role="dialog" aria-modal="true" aria-labelledby="startup-title" tabindex="-1" @keydown.tab.stop="trap" @keydown.esc.stop="canClose && emit('close')">
    <aside class="startup-brand"><div class="startup-wordmark"><img src="/brand/mumu.png" alt="" /><strong>沐沐</strong><span>MUMU</span></div><BrandOrbit /></aside>
    <div class="startup-workflow">
      <header class="startup-header"><nav aria-label="初始化步骤"><button v-for="(label, index) in ['环境检测', '模型配置', '平台授权']" :key="label" type="button" :class="{ active: step === index, done: step > index }" :disabled="index > step" :aria-current="step === index ? 'step' : undefined" @click="step = index"><span><Check v-if="step > index" :size="12" /><template v-else>{{ index + 1 }}</template></span>{{ label }}</button></nav><button v-if="canClose" class="icon-button" type="button" aria-label="退出初始化" title="退出初始化" @click="emit('close')"><X :size="18" /></button></header>
      <div class="startup-content">
        <div class="startup-heading"><span class="startup-step">{{ String(step + 1).padStart(2, '0') }}</span><h1 id="startup-title">{{ ['准备运行环境', '连接你的模型', '加入学习平台'][step] }}</h1></div>
        <section v-show="step === 0" class="startup-environment" :aria-busy="checking">
          <div v-if="!state.items.length && checking" class="startup-wait" role="status"><LoaderCircle class="spinning" :size="22" />正在检测本地环境</div>
          <div v-for="item in state.items" :key="item.id" class="dependency-row" :data-state="item.status"><div class="dependency-label"><Check v-if="item.status === 'ready'" class="dependency-ready" :size="18" /><LoaderCircle v-else-if="item.status === 'checking'" class="spinning" :size="18" /><CircleAlert v-else class="dependency-error" :size="18" /><strong>{{ item.label }}</strong><span>{{ item.status === 'ready' ? '就绪' : item.status === 'checking' ? '检测中' : '需处理' }}</span></div><p>{{ item.detail }}</p><div v-if="item.status !== 'ready' && item.command" class="dependency-command"><code>{{ item.command }}</code><button class="icon-button" type="button" :title="copied === item.id ? '已复制' : '复制手动命令'" :aria-label="'复制 ' + item.label + ' 手动命令'" @click="copy(item.command, item.id)"><Check v-if="copied === item.id" :size="15" /><Copy v-else :size="15" /></button></div></div>
          <p v-if="state.status === 'starting'" role="status" class="startup-status"><LoaderCircle class="spinning" :size="15" />正在启动本地服务</p>
          <details v-if="state.output && state.status === 'error'"><summary>安装输出</summary><pre class="dependency-output">{{ state.output }}</pre></details>
          <div v-if="state.recoveryCommand && state.status === 'error'" class="dependency-command"><code>{{ state.recoveryCommand }}</code><button class="icon-button" type="button" aria-label="复制手动启动命令" @click="copy(state.recoveryCommand, 'startup')"><Copy :size="15" /></button></div>
          <div class="dependency-actions"><button class="button light" type="button" :disabled="checking" @click="check()"><RefreshCw :class="{spinning: checking}" :size="14" />重新检测</button><button v-if="native && installable" class="button dark" type="button" :disabled="checking" @click="check(true)"><Download :size="14" />安装缺少的依赖</button><span v-if="ready" class="startup-status dependency-ready"><Check :size="14" />环境已就绪</span></div>
        </section>
        <ModelConfiguration v-if="ready" v-show="step === 1" @saved="modelSaved" />
        <section v-if="step === 2" class="startup-platforms"><div class="startup-platform-grid"><button v-for="item in platformCatalog" :key="item.name" class="startup-platform" :class="{selected: platform === item.name}" type="button" :disabled="pendingAuth" :aria-pressed="platform === item.name" @click="platform = item.name"><PlatformLogo :platform="item.name" :size="32" /><span><strong>{{ item.label }}</strong><small>{{ businessTypes.filter(type => item[type.id]).map(type => type.label).join(' · ') }}</small></span><Check v-if="platform === item.name" :size="16" /></button></div><div class="startup-authorization"><button class="button dark" type="button" :disabled="pendingAuth" @click="authorize"><LoaderCircle v-if="pendingAuth" class="spinning" :size="15" /><LogIn v-else :size="15" />{{ pendingAuth ? '等待授权' : '授权 ' + currentPlatform.label }}</button><span v-if="pendingAuth" role="status">{{ auth.status === 'verifying' ? '正在同步名称与头像' : '请在登录窗口完成授权' }}</span><p v-if="auth.status === 'error'" class="validation-error" role="alert">{{ auth.error }}</p></div><div v-if="accountList.length" class="startup-accounts"><div v-for="account in accountList" :key="account.id"><AccountAvatar :account="account" /><strong>{{ account.name }}</strong><Check v-if="account.status === 'ready'" :size="14" /><span>{{ account.status === 'ready' ? '已授权' : '待授权' }}</span></div></div></section>
        <p v-if="error || state.error && step === 0" class="startup-error" role="alert"><CircleAlert :size="16" />{{ error || state.error }}</p>
      </div>
      <footer class="startup-footer"><button v-if="step > 0" class="button light" type="button" :disabled="finishing" @click="step--"><ArrowLeft :size="14" />上一步</button><span v-else /><div><button v-if="step > 0" class="text-button" type="button" :disabled="finishing" @click="next(true)">{{ step === 2 ? '跳过，进入工作区' : '稍后配置' }}</button><button class="button dark" type="button" :disabled="finishing || (step === 0 && !ready) || (step === 1 && !configured) || (step === 2 && (pendingAuth || !accountList.some(account => account.status === 'ready')))" @click="next()"><LoaderCircle v-if="finishing" class="spinning" :size="14" />{{ step === 2 ? '开始使用' : '下一步' }}<ArrowRight v-if="!finishing" :size="14" /></button></div></footer>
    </div>
  </section></div></Teleport>
</template>

<style scoped>
/* finesse · component: mumu-first-run · register=product · palette=existing-workbench+glass-cyan · motion=orbit+step */
.startup-backdrop{position:fixed;inset:0;z-index:90;display:grid;place-items:center;background:var(--bg);padding:32px}.startup-experience{width:min(1180px,100%);height:min(800px,calc(100dvh - 64px));display:grid;grid-template-columns:minmax(280px,.8fr) minmax(500px,1.3fr);overflow:hidden;border:1px solid var(--line-strong);border-radius:8px;background:var(--surface);box-shadow:var(--shadow-modal);outline:none;animation:setup-arrive .5s ease both}.startup-brand{display:grid;grid-template-rows:auto minmax(0,1fr);min-width:0;background:#edf4f5;border-right:1px solid var(--line);padding:32px 28px}.startup-wordmark{display:flex;align-items:center;gap:8px}.startup-wordmark img{width:45px;height:45px;object-fit:contain}.startup-wordmark strong{font-size:23px}.startup-wordmark span{margin-left:auto;font-size:11px;color:var(--muted)}.startup-brand :deep(.brand-orbit){align-self:center;width:calc(100% + 56px);margin-left:-28px;aspect-ratio:1.05}.startup-workflow{display:flex;flex-direction:column;min-width:0;min-height:0}.startup-header{display:flex;align-items:center;justify-content:space-between;gap:14px;padding:28px 32px;border-bottom:1px solid var(--line)}.startup-header nav{display:flex;gap:22px}.startup-header button{display:flex;align-items:center;gap:8px;padding:0;background:none;color:var(--muted);font-size:12px;border:0}.startup-header button>span{width:23px;height:23px;border:1px solid var(--line-strong);border-radius:50%;display:grid;place-items:center;font-size:11px}.startup-header button.active{color:var(--ink)}.startup-header button.active>span,.startup-header button.done>span{background:var(--accent);border-color:var(--accent);color:white}.startup-content{flex:1;min-height:0;overflow:auto;padding:30px 32px}.startup-heading{display:flex;gap:13px;align-items:center;margin-bottom:28px}.startup-step{font-size:11px;font-variant-numeric:tabular-nums;color:var(--muted)}.startup-heading h1{font-size:23px;font-weight:600}.dependency-row{border-bottom:1px solid var(--line);padding:18px 0}.dependency-label{display:flex;align-items:center;gap:10px;font-size:13px}.dependency-label>span{margin-left:auto;color:var(--muted);font-size:11px}.dependency-ready{color:var(--green)}.dependency-error{color:var(--orange)}.dependency-row p{margin:9px 0 0 28px;color:var(--muted);font-size:11px;line-height:1.7;overflow-wrap:anywhere;white-space:pre-wrap;max-height:100px;overflow:auto}.dependency-command{display:flex;align-items:flex-start;gap:8px;background:var(--bg);border:1px solid var(--line);padding:10px;margin:12px 0 0 28px}.dependency-command code{flex:1;min-width:0;overflow-wrap:anywhere;font-size:11px;line-height:1.7;user-select:text}.dependency-output{max-height:120px;overflow:auto;white-space:pre-wrap;font-size:11px;background:var(--bg);padding:12px}.dependency-actions{display:flex;gap:10px;margin-top:24px;flex-wrap:wrap}.startup-status,.startup-wait{display:flex;align-items:center;gap:8px;font-size:12px}.startup-status{margin-top:15px}.startup-platform-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.startup-platform{display:flex;align-items:center;gap:11px;border:1px solid var(--line);border-radius:6px;padding:14px 12px;text-align:left;background:var(--surface);transition:background .16s,border-color .16s}.startup-platform:hover,.startup-platform.selected{background:var(--accent-soft);border-color:var(--accent)}.startup-platform>.platform-logo{flex:none}.startup-platform>span:not(.platform-logo){min-width:0;flex:1}.startup-platform strong{font-size:12px;font-weight:600;display:block}.startup-platform small{font-size:10px;color:var(--muted);display:block;margin-top:6px}.startup-platform>svg{color:var(--accent)}.startup-authorization{display:flex;flex-wrap:wrap;gap:12px;align-items:center;margin-top:22px}.startup-authorization>span{font-size:11px;color:var(--muted)}.startup-accounts{margin-top:20px;border-top:1px solid var(--line)}.startup-accounts>div{display:flex;align-items:center;gap:10px;padding:12px 0;border-bottom:1px solid var(--line);font-size:12px}.startup-accounts>div>span:last-child{margin-left:auto;font-size:11px;color:var(--green)}.startup-error{display:flex;gap:8px;align-items:flex-start;color:var(--red);font-size:12px;line-height:1.7;overflow-wrap:anywhere;margin-top:16px}.startup-error svg{flex:none;margin-top:2px}.startup-footer{display:flex;align-items:center;justify-content:space-between;gap:12px;border-top:1px solid var(--line);padding:20px 32px}.startup-footer>div{display:flex;align-items:center;gap:16px}.startup-content :deep(.model-configuration){padding:0}.startup-content :deep(.preference-form-grid){grid-template-columns:minmax(0,1fr)}.startup-content :deep(.preference-section-heading){margin-top:22px}.startup-content :deep(.preferences-save-bar){position:static;flex-wrap:wrap}.startup-experience button:focus-visible{outline:2px solid var(--accent);outline-offset:3px}.startup-experience button:disabled{opacity:.45;cursor:not-allowed}@keyframes setup-arrive{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:none}}@media(max-width:1100px){.startup-backdrop{padding:18px}.startup-experience{grid-template-columns:240px minmax(430px,1fr);height:calc(100dvh - 36px)}.startup-content,.startup-header{padding:24px}.startup-brand{padding:26px 20px}.startup-footer{padding:18px 24px}.startup-header nav{gap:16px}}@media(prefers-reduced-motion:reduce){.startup-experience{animation:none}.startup-platform{transition:none}}
</style>
