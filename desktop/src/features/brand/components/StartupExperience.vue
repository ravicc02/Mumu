<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue';
import { ArrowLeft, ArrowRight, Check, CircleAlert, Copy, Download, LoaderCircle, LogIn, RefreshCw, X } from '@lucide/vue';
import ModelConfiguration from '../../settings/components/ModelConfiguration.vue';
import PlatformLogo from '../../platforms/components/PlatformLogo.vue';
import AccountAvatar from '../../accounts/components/AccountAvatar.vue';
import { api } from '../../../app/api.js';
import { copyText } from '../../../app/lib/clipboard.js';
import { platformCatalog, businessTypes } from '../../platforms/lib/catalog.js';
import { createAccountAuthorization } from '../../accounts/lib/authorization.js';
import { completeOnboarding, environmentReady } from '../../../app/lib/onboarding.js';

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
const stepTitles = ['准备浏览器环境', '连接模型服务', '授权学习平台'];
const stepSubtitles = [
  '检查浏览器内核、自动化组件与任务运行库。',
  '配置一个可用的模型服务，用于题目与课程内容处理。',
  '选择要接入的学习平台，授权后即可开始执行任务。',
];
const setupItems = computed(() => state.value.items
  .filter(item => item.id !== 'storage')
  .map(item => {
    if (item.id === 'runtime') {
      return {
        ...item,
        label: '运行库与自动化组件',
        detail: item.status === 'ready'
          ? (native ? '内置后端、Playwright 与 Browser Use 已加载' : 'Python、Playwright、Browser Use 与业务运行库已加载')
          : item.detail,
      };
    }
    if (item.id === 'browser') {
      return {
        ...item,
        label: '浏览器环境',
        detail: item.status === 'ready' ? 'Chrome / Edge 可用于课程与任务执行' : item.detail,
      };
    }
    return item;
  }));
const storageIssue = computed(() => state.value.items.find(item => item.id === 'storage' && item.status !== 'ready'));
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
  error.value = ''; copied.value = '';
  try { await copyText(command); copied.value = id; }
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
    const choices = { model: modelChoice.value, platform: skip ? 'skipped' : platformChoice.value };
    let localError = null, serverError = null;
    try { completeOnboarding(info.data_root || props.scope || state.value.scope, choices); }
    catch (failure) { localError = failure; }
    try { await api.completeOnboarding(choices); }
    catch (failure) { serverError = failure; }
    if (localError && serverError) throw localError;
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
  <Teleport to="body">
    <div class="startup-backdrop">
      <section ref="dialog" class="startup-experience" role="dialog" aria-modal="true" aria-labelledby="startup-title" tabindex="-1" @keydown.tab.stop="trap" @keydown.esc.stop="canClose && emit('close')">
        <header class="startup-header">
          <nav aria-label="初始化步骤">
            <button v-for="(label, index) in ['浏览器环境', '模型服务', '平台账号']" :key="label" type="button" :class="{ active: step === index, done: step > index }" :disabled="index > step" :aria-current="step === index ? 'step' : undefined" @click="step = index">
              <span><Check v-if="step > index" :size="12" /><template v-else>{{ index + 1 }}</template></span>{{ label }}
            </button>
          </nav>
          <button v-if="canClose" class="icon-button startup-close" type="button" aria-label="退出初始化" title="退出初始化" @click="emit('close')"><X :size="18" /></button>
        </header>

        <main class="startup-content">
          <div class="startup-stage">
            <div class="startup-heading">
              <span class="startup-step">{{ String(step + 1).padStart(2, '0') }}</span>
              <div><h1 id="startup-title">{{ stepTitles[step] }}</h1><p>{{ stepSubtitles[step] }}</p></div>
            </div>

            <section v-show="step === 0" class="startup-environment" :aria-busy="checking">
              <div v-if="!state.items.length && checking" class="startup-wait" role="status"><LoaderCircle class="spinning" :size="22" />正在检查浏览器与自动化组件</div>
              <div v-for="item in setupItems" :key="item.id" class="dependency-row" :data-state="item.status">
                <div class="dependency-label">
                  <Check v-if="item.status === 'ready'" class="dependency-ready" :size="18" />
                  <LoaderCircle v-else-if="item.status === 'checking'" class="spinning" :size="18" />
                  <CircleAlert v-else class="dependency-error" :size="18" />
                  <strong>{{ item.label }}</strong>
                  <span>{{ item.status === 'ready' ? '已就绪' : item.status === 'checking' ? '准备中' : '需处理' }}</span>
                </div>
                <p>{{ item.detail }}</p>
                <div v-if="item.status !== 'ready' && item.command" class="dependency-command">
                  <code>{{ item.command }}</code>
                  <button class="icon-button" type="button" :title="copied === item.id ? '已复制' : '复制手动命令'" :aria-label="'复制 ' + item.label + ' 手动命令'" @click="copy(item.command, item.id)"><Check v-if="copied === item.id" :size="15" /><Copy v-else :size="15" /></button>
                </div>
              </div>
              <p v-if="storageIssue" class="startup-error" role="alert"><CircleAlert :size="16" />本地工作区不可写：{{ storageIssue.detail }}</p>
              <details v-if="state.output && state.status === 'error'"><summary>安装输出</summary><pre class="dependency-output">{{ state.output }}</pre></details>
              <div v-if="state.recoveryCommand && state.status === 'error'" class="dependency-command"><code>{{ state.recoveryCommand }}</code><button class="icon-button" type="button" aria-label="复制手动启动命令" @click="copy(state.recoveryCommand, 'startup')"><Copy :size="15" /></button></div>
              <div class="dependency-actions">
                <button class="button light" type="button" :disabled="checking" @click="check()"><RefreshCw :class="{spinning: checking}" :size="14" />重新检测</button>
                <button v-if="native && installable" class="button dark" type="button" :disabled="checking" @click="check(true)"><Download :size="14" />安装缺少的组件</button>
                <span v-if="ready" class="startup-status dependency-ready"><Check :size="14" />浏览器与运行库已就绪</span>
              </div>
            </section>

            <ModelConfiguration v-if="ready" v-show="step === 1" @saved="modelSaved" />

            <section v-if="step === 2" class="startup-platforms">
              <div class="startup-platform-grid">
                <button v-for="item in platformCatalog" :key="item.name" class="startup-platform" :class="{selected: platform === item.name}" type="button" :disabled="pendingAuth" :aria-pressed="platform === item.name" @click="platform = item.name">
                  <PlatformLogo :platform="item.name" :size="32" />
                  <span><strong>{{ item.label }}</strong><small>{{ businessTypes.filter(type => item[type.id]).map(type => type.label).join(' · ') }}</small></span>
                  <Check v-if="platform === item.name" :size="16" />
                </button>
              </div>
              <div class="startup-authorization">
                <button class="button dark" type="button" :disabled="pendingAuth" @click="authorize"><LoaderCircle v-if="pendingAuth" class="spinning" :size="15" /><LogIn v-else :size="15" />{{ pendingAuth ? '等待授权' : '授权 ' + currentPlatform.label }}</button>
                <span v-if="pendingAuth" role="status">{{ auth.status === 'verifying' ? '正在同步名称与头像' : '请在登录窗口完成授权' }}</span>
                <p v-if="auth.status === 'error'" class="validation-error" role="alert">{{ auth.error }}</p>
              </div>
              <div v-if="accountList.length" class="startup-accounts">
                <div v-for="account in accountList" :key="account.id"><AccountAvatar :account="account" /><strong>{{ account.name }}</strong><Check v-if="account.status === 'ready'" :size="14" /><span>{{ account.status === 'ready' ? '已授权' : '待授权' }}</span></div>
              </div>
            </section>

            <p v-if="error || state.error && step === 0" class="startup-error" role="alert"><CircleAlert :size="16" />{{ error || state.error }}</p>
          </div>
        </main>

        <footer class="startup-footer">
          <div class="startup-footer-inner">
            <button v-if="step > 0" class="button light" type="button" :disabled="finishing" @click="step--"><ArrowLeft :size="14" />上一步</button>
            <span v-else />
            <div>
              <button v-if="step > 0" class="text-button" type="button" :disabled="finishing" @click="next(true)">{{ step === 2 ? '跳过，进入工作区' : '稍后配置' }}</button>
              <button class="button dark" type="button" :disabled="finishing || (step === 0 && !ready) || (step === 1 && !configured) || (step === 2 && (pendingAuth || !accountList.some(account => account.status === 'ready')))" @click="next()"><LoaderCircle v-if="finishing" class="spinning" :size="14" />{{ step === 2 ? '开始使用' : '下一步' }}<ArrowRight v-if="!finishing" :size="14" /></button>
            </div>
          </div>
        </footer>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.startup-backdrop { position: fixed; inset: 0; z-index: var(--ds-z-dialog); background: var(--surface); }
.startup-experience { display: grid; grid-template-rows: 72px minmax(0,1fr) 78px; width: 100%; height: 100dvh; overflow: hidden; outline: none; animation: setup-arrive .32s ease both; }
.startup-header { position: relative; display: flex; align-items: center; justify-content: center; width: min(1120px, calc(100% - 64px)); margin: 0 auto; border-bottom: 1px solid var(--line); padding: 0 42px; }
.startup-header nav { display: flex; align-items: center; justify-content: center; gap: 8px; }
.startup-header nav button { display: flex; align-items: center; gap: 8px; min-height: 40px; border: 1px solid transparent; border-radius: var(--radius-control); background: transparent; padding: 6px 12px; color: var(--muted); font-size: var(--text-sm); }
.startup-header nav button:hover:not(:disabled) { background: var(--surface-muted); color: var(--ink); }
.startup-header nav button>span { display: grid; place-items: center; width: 24px; height: 24px; border: 1px solid var(--line-strong); border-radius: 50%; font-size: var(--text-xs); }
.startup-header nav button.active { border-color: var(--line); background: var(--surface); color: var(--ink); }
.startup-header nav button.active>span,.startup-header nav button.done>span { border-color: var(--accent); background: var(--accent); color: var(--on-accent); }
.startup-header nav button:disabled { opacity: .38; cursor: not-allowed; }
.startup-close { position: absolute; right: 0; top: 50%; transform: translateY(-50%); }
.startup-content { min-height: 0; overflow-y: auto; padding: 42px 32px 34px; }
.startup-stage { width: min(880px, 100%); min-height: 100%; margin: 0 auto; }
.startup-heading { display: grid; grid-template-columns: 32px minmax(0,1fr); gap: 12px; align-items: start; margin-bottom: 30px; }
.startup-step { padding-top: 8px; color: var(--muted); font-size: var(--text-xs); font-variant-numeric: tabular-nums; }
.startup-heading h1 { font-size: var(--ds-text-3xl); font-weight: 500; line-height: 1.3; }
.startup-heading p { max-width: 620px; margin-top: 8px; color: var(--muted); font-size: var(--text-sm); line-height: 1.7; }
.startup-environment { width: min(720px, 100%); }
.dependency-row { display: grid; grid-template-columns: minmax(0,1fr) auto; gap: 4px 18px; border-top: 1px solid var(--line); padding: 18px 2px; }
.dependency-row:last-of-type { border-bottom: 1px solid var(--line); }
.dependency-label { display: flex; align-items: center; gap: 10px; grid-column: 1; font-size: var(--ds-text-base); font-weight: 500; }
.dependency-label>span { margin-left: auto; color: var(--muted); font-size: var(--text-xs); white-space: nowrap; }
.dependency-row>p { grid-column: 1 / -1; margin: 0 0 0 27px; color: var(--muted); font-size: var(--text-xs); line-height: 1.7; overflow-wrap: anywhere; }
.dependency-ready { color: var(--green); }
.dependency-error { color: var(--orange); }
.dependency-command { display: flex; grid-column: 1 / -1; align-items: flex-start; gap: 8px; margin: 10px 0 0 28px; border: 1px solid var(--line); border-radius: var(--radius-control); background: var(--bg); padding: 10px; }
.dependency-command code { min-width: 0; flex: 1; overflow-wrap: anywhere; color: var(--muted); font: var(--text-xs)/1.7 "Segoe UI", sans-serif; user-select: text; }
.dependency-output { max-height: 130px; overflow: auto; margin-top: 16px; background: var(--bg); padding: 12px; white-space: pre-wrap; font-size: var(--text-xs); }
.dependency-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; margin-top: 24px; }
.startup-status,.startup-wait { display: flex; align-items: center; gap: 8px; color: var(--muted); font-size: var(--text-sm); }
.startup-wait { min-height: 120px; }
.startup-platform-grid { display: grid; grid-template-columns: repeat(3,minmax(0,1fr)); gap: 12px; }
.startup-platform { display: grid; grid-template-columns: 32px minmax(0,1fr) 16px; align-items: center; gap: 12px; min-height: 84px; border: 1px solid var(--line); border-radius: var(--radius-card); background: var(--surface); padding: 14px 16px; text-align: left; transition: background var(--ds-motion-swift),border-color var(--ds-motion-swift); }
.startup-platform:hover,.startup-platform.selected { border-color: var(--accent); background: var(--accent-soft); }
.startup-platform strong { display: block; font-size: var(--ds-text-base); font-weight: 500; }
.startup-platform small { display: block; margin-top: 4px; color: var(--muted); font-size: var(--text-xs); line-height: 1.5; }
.startup-platform>svg { color: var(--accent); }
.startup-authorization { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; margin-top: 22px; }
.startup-authorization>span { color: var(--muted); font-size: var(--text-xs); }
.startup-accounts { margin-top: 22px; border-top: 1px solid var(--line); }
.startup-accounts>div { display: flex; align-items: center; gap: 10px; border-bottom: 1px solid var(--line); padding: 12px 0; font-size: var(--text-sm); }
.startup-accounts>div>span:last-child { margin-left: auto; color: var(--green); font-size: var(--text-xs); }
.startup-error { display: flex; align-items: flex-start; gap: 8px; margin-top: 16px; color: var(--red); font-size: var(--text-sm); line-height: 1.7; overflow-wrap: anywhere; }
.startup-error svg { flex: none; margin-top: 2px; }
.startup-footer { border-top: 1px solid var(--line); background: var(--surface); }
.startup-footer-inner { display: flex; align-items: center; justify-content: space-between; gap: 12px; width: min(1120px, calc(100% - 64px)); height: 100%; margin: 0 auto; padding: 0 4px; }
.startup-footer-inner>div { display: flex; align-items: center; gap: 16px; }
.startup-experience :deep(.model-configuration) { width: min(760px,100%); padding: 0; }
.startup-experience :deep(.preference-form-grid) { grid-template-columns: minmax(0,1fr); gap: 0; }
.startup-experience :deep(.preferences-save-bar) { position: static; flex-wrap: wrap; }
.startup-experience button:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
.startup-experience button:disabled { opacity: .45; cursor: not-allowed; }
@keyframes setup-arrive { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
@media(max-width:900px){.startup-header,.startup-footer-inner{width:calc(100% - 32px)}.startup-header nav button{padding-inline:6px}.startup-header nav button>span{display:none}.startup-platform-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:680px){.startup-experience{grid-template-rows:64px minmax(0,1fr) 70px}.startup-content{padding:28px 18px}.startup-header{padding-inline:34px}.startup-header nav button{font-size: var(--text-xs)}.startup-platform-grid{grid-template-columns:1fr}.startup-footer-inner>div{gap:8px}}
@media(prefers-reduced-motion:reduce){.startup-experience{animation:none}.startup-platform{transition:none}}
</style>
