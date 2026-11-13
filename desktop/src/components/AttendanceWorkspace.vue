<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue';
import { CheckCheck, CircleAlert, ClipboardCheck, Copy, ExternalLink, ImageUp, Link, LoaderCircle, Play, RefreshCw, Search, Share2, ShieldCheck, Square, X } from '@lucide/vue';
import QRCode from 'qrcode';
import { api } from '../api';
import { accountsForBusiness } from '../lib/platforms';
import { attendanceKinds, attendanceStates, attendanceRunning, attendanceRunnable, attendanceWindowOpen, attendanceWindowLabel, attendanceLocation, readAttendanceQr } from '../lib/attendance';
import AccountAvatar from './AccountAvatar.vue';

const props = defineProps({ accounts: { type: Array, default: () => [] } });
const emit = defineEmits(['authorize']);
const accounts = computed(() => accountsForBusiness(props.accounts, 'attendance'));
const selectedAccounts = ref([]), selectedActivities = ref([]), activities = ref([]), batches = ref([]), currentId = ref('');
const clockMs = ref(Date.now());
const collecting = ref(new Set()), collectionNotes = ref({}), query = ref(''), type = ref(''), busy = ref(false), error = ref(''), loadError = ref(''), message = ref('');
const code = ref(''), qr = ref(''), fileInput = ref(null), decoding = ref(false);
const latitude = ref(''), longitude = ref(''), address = ref(''), locationSource = ref('manual'), locating = ref(false);
const needsLocation = computed(() => activities.value.some(row => selectedActivities.value.includes(row.id) && row.kind === 'location') || current.value?.items.some(item => item.kind === 'location' && !['succeeded', 'already_signed', 'expired'].includes(item.status)));
function signInputs() { return { code: code.value.trim(), qr: qr.value.trim(), location: needsLocation.value ? attendanceLocation(latitude.value, longitude.value, address.value, locationSource.value) : null }; }
function locate() {
  if (!navigator.geolocation) { error.value = '当前浏览器不支持定位，请填写实际位置'; return; }
  locating.value = true; error.value = '';
  navigator.geolocation.getCurrentPosition(position => {
    if (disposed) return;
    latitude.value = position.coords.latitude; longitude.value = position.coords.longitude;
    locationSource.value = 'browser'; locating.value = false;
  }, () => { if (!disposed) { locating.value = false; error.value = '未取得当前位置，请允许定位或手动填写'; } }, { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 });
}
const shareOpen = ref(false), shareMinutes = ref(15), shareBase = ref(''), shareUrl = ref(''), shareImage = ref(''), shareBusy = ref(false), shareError = ref(''), shareInput = ref(null);
let timer, disposed = false, refreshing = false, initializedAccounts = false;
const current = computed(() => batches.value.find(batch => batch.id === currentId.value));
const rows = computed(() => activities.value.filter(row => attendanceWindowOpen(row, clockMs.value) && selectedAccounts.value.includes(row.account_id) && (!type.value || type.value === row.kind) && [row.title, row.course_name, row.account_name].join(' ').toLowerCase().includes(query.value.toLowerCase())));
const allSelected = computed(() => rows.value.length > 0 && rows.value.every(row => selectedActivities.value.includes(row.id)));
const allAccounts = computed(() => { const ready = accounts.value.filter(account => account.status === 'ready'); return ready.length > 0 && ready.every(account => selectedAccounts.value.includes(account.id)); });
const label = status => attendanceStates[status] || status;
const batchLabel = batch => batch.status === 'completed' ? '已完成' : batch.status === 'running' ? '执行中' : batch.status === 'needs_review' ? '待核对' : batch.status === 'awaiting_input' ? '待执行／补充' : '已结束';
const time = value => new Date(typeof value === 'number' ? value * 1000 : value).toLocaleString();
function toggleAccount(id) { selectedAccounts.value = selectedAccounts.value.includes(id) ? selectedAccounts.value.filter(value => value !== id) : [...selectedAccounts.value, id]; }
function toggleAllAccounts() { selectedAccounts.value = allAccounts.value ? [] : accounts.value.filter(account => account.status === 'ready').map(account => account.id); }
function accountNote(account) {
  if (collecting.value.has(account.id)) return '采集中';
  const note = collectionNotes.value[account.id];
  if (note?.includes('失败')) return note;
  const count = activities.value.filter(row => row.account_id === account.id && attendanceWindowOpen(row, clockMs.value)).length;
  return note || count ? count + ' 个可签到' : account.status === 'ready' ? '已授权' : '待授权';
}
function toggleRows() { const ids = new Set(rows.value.map(row => row.id)); selectedActivities.value = allSelected.value ? selectedActivities.value.filter(id => !ids.has(id)) : [...new Set([...selectedActivities.value, ...ids])]; }
async function refresh() {
  clockMs.value = Date.now();
  if (refreshing) return; refreshing = true;
  try {
    const [nextActivities, nextBatches] = await Promise.all([api.attendanceActivities(), api.attendanceBatches()]);
    if (disposed) return;
    activities.value = nextActivities; batches.value = nextBatches; loadError.value = '';
    if (!nextBatches.some(batch => batch.id === currentId.value)) currentId.value = nextBatches[0]?.id || '';
    const available = new Set(nextActivities.map(row => row.id)); selectedActivities.value = selectedActivities.value.filter(id => available.has(id));
  } catch (cause) { if (!disposed) loadError.value = cause.message; }
  finally { refreshing = false; }
}
async function collect() {
  error.value = ''; message.value = '';
  const ids = selectedAccounts.value.filter(id => !collecting.value.has(id));
  await Promise.all(ids.map(async id => {
    collecting.value = new Set([...collecting.value, id]); collectionNotes.value[id] = '采集中';
    try {
      const result = await api.collectAttendance(id);
      collectionNotes.value[id] = result.errors.length ? result.errors.length + ' 个课程失败' : result.activities.length + ' 个进行中';
      if (result.errors.length) error.value = result.errors.map(item => item.course_name + '：' + item.message).slice(0, 3).join('；');
    } catch (cause) { collectionNotes.value[id] = '采集失败'; error.value = cause.message; }
    finally { collecting.value = new Set([...collecting.value].filter(value => value !== id)); if (!disposed) await refresh(); }
  }));
}
async function createBatch() {
  const visible = new Set(rows.value.map(row => row.id));
  const ids = selectedActivities.value.filter(id => visible.has(id));
  if (!ids.length) throw new Error('请先勾选签到活动');
  if (ids.length > 100) throw new Error('每个批次最多 100 项，请分批执行');
  const batch = await api.createAttendanceBatch(ids);
  batches.value = [batch, ...batches.value]; currentId.value = batch.id; selectedActivities.value = [];
  return batch;
}
async function executeSelection() {
  if (busy.value) return; busy.value = true; error.value = '';
  try { const inputs = signInputs(); const batch = await createBatch(); await api.attendanceAction(batch.id, 'execute', inputs); await refresh(); }
  catch (cause) { error.value = cause.message; } finally { busy.value = false; }
}
async function action(name) {
  if (!current.value || busy.value) return; busy.value = true; error.value = '';
  try { await api.attendanceAction(current.value.id, name, name === 'execute' ? signInputs() : {}); await refresh(); }
  catch (cause) { error.value = cause.message; } finally { busy.value = false; }
}
async function openShare(selection = false) {
  if (busy.value) return; busy.value = true; error.value = '';
  try { if (selection) await createBatch(); shareOpen.value = true; shareUrl.value = ''; shareImage.value = ''; shareError.value = ''; await nextTick(); shareInput.value?.focus(); }
  catch (cause) { error.value = cause.message; } finally { busy.value = false; }
}
async function generateShare() {
  if (!current.value || shareBusy.value) return; shareBusy.value = true; shareError.value = '';
  try { const result = await api.shareAttendance(current.value.id, { minutes: shareMinutes.value, base_url: shareBase.value.trim() }); shareUrl.value = result.url; shareImage.value = await QRCode.toDataURL(result.url, { width: 200, margin: 2 }); await refresh(); }
  catch (cause) { shareError.value = cause.message; } finally { shareBusy.value = false; }
}
async function revoke(id) { try { await api.revokeAttendanceShare(id); message.value = '分享已撤销'; shareUrl.value = ''; shareImage.value = ''; await refresh(); } catch (cause) { error.value = cause.message; } }
async function copy() { try { await navigator.clipboard.writeText(shareUrl.value); shareError.value = ''; message.value = '分享链接已复制'; } catch { shareError.value = '未能复制，请选中链接手动复制'; } }
async function image(event) {
  const file = event.target.files?.[0]; if (!file) return; decoding.value = true; error.value = '';
  try { qr.value = await readAttendanceQr(file); message.value = '二维码已识别'; } catch (cause) { error.value = cause.message; } finally { decoding.value = false; event.target.value = ''; }
}
async function openActivity(id) { try { await api.openAttendance(id); } catch (cause) { error.value = cause.message; } }
watch(accounts, values => { const ids = new Set(values.map(account => account.id)); selectedAccounts.value = selectedAccounts.value.filter(id => ids.has(id)); if (!initializedAccounts && values.some(account => account.status === 'ready')) { selectedAccounts.value = values.filter(account => account.status === 'ready').map(account => account.id); initializedAccounts = true; } }, { immediate: true });
watch(rows, values => { const ids = new Set(values.map(row => row.id)); selectedActivities.value = selectedActivities.value.filter(id => ids.has(id)); });
onMounted(() => { refresh(); timer = setInterval(refresh, 2500); });
onUnmounted(() => { disposed = true; clearInterval(timer); });
</script>

<template>
  <main class="attendance-workspace">
    <aside class="attendance-accounts">
      <div class="section-title"><h3>签到账号</h3><span class="count">{{ accounts.length }}</span></div>
      <label class="attendance-select-all"><input type="checkbox" :checked="allAccounts && accounts.length > 0" @change="toggleAllAccounts">全部账号</label>
      <label v-for="account in accounts" :key="account.id" class="attendance-account" :class="{ selected: selectedAccounts.includes(account.id) }">
        <input type="checkbox" :value="account.id" :checked="selectedAccounts.includes(account.id)" :disabled="account.status !== 'ready'" @change="toggleAccount(account.id)">
        <AccountAvatar :account="account" :size="28"/><span class="attendance-account-name"><strong>{{ account.name }}</strong><small>{{ accountNote(account) }}</small></span><LoaderCircle v-if="collecting.has(account.id)" :size="13" class="spin"/>
      </label>
      <div v-if="!accounts.length" class="empty-inline"><p>请先授权学习通账号</p><button class="button light compact" type="button" @click="emit('authorize')">授权账号</button></div>
      <div class="attendance-history"><div class="section-title"><h3>批次记录</h3><span class="count">{{ batches.length }}</span></div>
        <button v-for="batch in batches" :key="batch.id" type="button" :class="{ selected: currentId === batch.id }" @click="currentId = batch.id"><span>{{ batch.completed }} / {{ batch.total }} · {{ batchLabel(batch) }}</span><small>{{ time(batch.created_at) }}</small></button>
      </div>
    </aside>
    <section class="attendance-board">
      <header class="attendance-heading"><h2><ClipboardCheck :size="21"/>签到 <span class="heading-count">{{ rows.length }}</span></h2><button class="button dark" type="button" :disabled="!selectedAccounts.length || selectedAccounts.every(id => collecting.has(id))" @click="collect"><RefreshCw :size="14" :class="{ spin: collecting.size }"/>{{ collecting.size ? '采集中 ' + collecting.size : '采集签到' }}</button></header>
      <div class="attendance-search"><label><Search :size="15"/><input v-model="query" placeholder="搜索签到、课程、账号" aria-label="搜索签到"></label><select v-model="type" aria-label="筛选签到方式"><option value="">全部类型</option><option v-for="(title, id) in attendanceKinds" :key="id" :value="id">{{ title }}</option></select></div>
      <p v-if="error || loadError" class="attendance-feedback error" role="alert"><CircleAlert :size="15"/>{{ error || loadError }}<button class="icon-button" type="button" aria-label="关闭错误" @click="error = ''; loadError = ''"><X :size="13"/></button></p>
      <p v-if="message" class="attendance-feedback success" role="status"><CheckCheck :size="15"/>{{ message }}</p>
      <div class="attendance-selection"><label><input type="checkbox" :checked="allSelected" :disabled="!rows.length" @change="toggleRows">全选</label><span>已选 {{ selectedActivities.length }} 项</span></div>
      <div class="attendance-activities"><article v-for="row in rows" :key="row.id" class="attendance-activity"><input v-model="selectedActivities" type="checkbox" :value="row.id" :aria-label="'选择 ' + row.account_name + ' ' + row.title"><div><strong>{{ row.title }}</strong><span>{{ row.course_name }}</span><small>{{ row.account_name }}</small><time>{{ attendanceWindowLabel(row) }}</time></div><span class="attendance-kind" :class="row.kind">{{ attendanceKinds[row.kind] }}</span><button class="icon-button" type="button" title="打开签到页面" aria-label="打开签到页面" @click="openActivity(row.id)"><ExternalLink :size="14"/></button></article>
        <div v-if="!rows.length" class="attendance-empty"><ClipboardCheck :size="30"/><strong>{{ collecting.size ? '正在采集签到活动' : '当前没有有效时段内的未签到活动' }}</strong><button v-if="!collecting.size" class="button light" type="button" :disabled="!selectedAccounts.length" @click="collect"><RefreshCw :size="14"/>采集签到</button></div>
      </div>
      <footer class="attendance-batch-actions"><button class="button light" type="button" :disabled="busy || !selectedActivities.length" @click="openShare(true)"><Share2 :size="14"/>分享签到</button><button class="button dark" type="button" :disabled="busy || !selectedActivities.length" @click="executeSelection"><Play :size="14"/>批量签到</button></footer>
    </section>
    <aside class="attendance-results">
      <header class="attendance-heading"><h3>执行结果</h3><span v-if="current" class="count">{{ current.completed }} / {{ current.total }}</span></header>
      <div class="attendance-inputs"><label>签到码／手势码<input v-model="code" inputmode="numeric" maxlength="16" autocomplete="off" aria-label="签到码或手势码" placeholder="按活动要求填写"></label><label>二维码内容<textarea v-model="qr" rows="3" maxlength="4096" aria-label="签到二维码内容" placeholder="粘贴二维码链接，或导入图片"></textarea></label><input ref="fileInput" hidden type="file" accept="image/png,image/jpeg,image/webp" @change="image"><button class="button light compact" type="button" :disabled="decoding" @click="fileInput?.click()"><ImageUp :size="14"/>{{ decoding ? '识别中' : '导入二维码' }}</button></div>
      <fieldset v-if="needsLocation" class="attendance-location"><legend>本次签到位置</legend>
        <button class="button light compact" type="button" :disabled="locating" @click="locate"><RefreshCw :size="14" :class="{ spin: locating }"/>{{ locating ? '正在定位' : '获取当前位置' }}</button>
        <div class="attendance-coordinate-row"><label>纬度<input v-model="latitude" type="number" step="any" min="-90" max="90" aria-label="签到纬度" @input="locationSource = 'manual'"></label><label>经度<input v-model="longitude" type="number" step="any" min="-180" max="180" aria-label="签到经度" @input="locationSource = 'manual'"></label></div>
        <label>实际位置<input v-model="address" maxlength="200" placeholder="填写实际所在地点" aria-label="签到实际位置"></label>
        <small>仅用于你确认处于此位置的所选账号。</small>
      </fieldset>
      <template v-if="current"><div class="attendance-run-actions"><button v-if="attendanceRunning(current)" class="button light compact" type="button" :disabled="busy" @click="action('cancel')"><Square :size="13"/>停止</button><button v-else class="button dark compact" type="button" :disabled="busy || !attendanceRunnable(current)" @click="action('execute')"><Play :size="13"/>继续执行</button><button class="icon-button" type="button" title="重新核对平台记录" aria-label="核对签到结果" :disabled="busy || attendanceRunning(current)" @click="action('reconcile')"><RefreshCw :size="15"/></button><button class="icon-button" type="button" title="创建分享链接" aria-label="创建分享链接" @click="openShare()"><Share2 :size="15"/></button></div>
        <div class="attendance-item-results"><article v-for="item in current.items" :key="item.id" :data-status="item.status"><div><strong>{{ item.account_name }}</strong><span class="attendance-status" :class="item.status">{{ label(item.status) }}</span></div><span>{{ item.course_name }} · {{ item.title }}</span><p>{{ item.message }}</p><button v-if="['manual_required','unknown','auth_required'].includes(item.status)" class="button light compact" type="button" @click="(item.status === 'auth_required' || item.message.includes('重新授权')) ? emit('authorize', item.account_id) : openActivity(item.activity_id)"><ExternalLink :size="12"/>{{ (item.status === 'auth_required' || item.message.includes('重新授权')) ? '管理授权' : '打开签到' }}</button></article></div>
        <section v-if="current.shares.length" class="attendance-share-list"><h4><Link :size="13"/>分享授权</h4><div v-for="share in current.shares" :key="share.id"><span>{{ share.revoked ? '已撤销' : share.expires_at * 1000 < Date.now() ? '已过期' : '有效至 ' + time(share.expires_at) }}</span><button v-if="!share.revoked && share.expires_at * 1000 > Date.now()" class="button ghost compact" type="button" @click="revoke(share.id)">撤销</button></div></section>
      </template><div v-else class="attendance-empty small"><ShieldCheck :size="28"/><span>尚无签到批次</span></div>
    </aside>
    <div v-if="shareOpen" class="modal-backdrop" @click.self="shareOpen = false"><section class="modal-card attendance-share-modal" role="dialog" aria-modal="true" aria-label="分享批量签到" @keydown.esc.stop="shareOpen = false"><header><h3><Share2 :size="18"/>分享批量签到</h3><button class="icon-button" type="button" aria-label="关闭分享窗口" @click="shareOpen = false"><X :size="17"/></button></header><p>{{ current?.total || 0 }} 项签到 · 仅授权当前批次</p><label>有效期<select ref="shareInput" v-model.number="shareMinutes"><option :value="5">5 分钟</option><option :value="15">15 分钟</option><option :value="30">30 分钟</option><option :value="60">1 小时</option></select></label><label>外网分享地址（可选）<input v-model="shareBase" placeholder="https://你的分享域名" aria-label="外网分享地址"></label><p class="attendance-share-network">留空生成局域网链接；跨网络访问需配置指向分享端口的 HTTPS 地址。</p><div v-if="shareUrl" class="attendance-share-output"><img :src="shareImage" alt="批量签到分享二维码"><div><input :value="shareUrl" readonly aria-label="签到分享链接"><button class="button light" type="button" @click="copy"><Copy :size="14"/>复制链接</button></div></div><p v-if="shareError" class="attendance-feedback error" role="alert">{{ shareError }}</p><footer><button class="button dark" type="button" :disabled="shareBusy" @click="generateShare"><LoaderCircle v-if="shareBusy" :size="14" class="spin"/><Link v-else :size="14"/>{{ shareUrl ? '生成新链接' : '创建链接' }}</button></footer></section></div>
  </main>
</template>

<style src="../attendance.css"></style>
