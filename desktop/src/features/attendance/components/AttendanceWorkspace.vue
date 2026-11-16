<script setup>
import ChoiceInput from "../../../shared/controls/ChoiceInput.vue";
import ToggleSwitch from "../../../shared/controls/ToggleSwitch.vue";
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue';
import {
  CheckCheck,
  CircleAlert,
  Clock3,
  Copy,
  Image,
  Link,
  LoaderCircle,
  MapPin,
  Pencil,
  Plus,
  RefreshCw,
  ScanFace,
  Trash2,
  Upload,
  Users,
  X,
} from '@lucide/vue';
import QRCode from 'qrcode';
import { api } from '../../../app/api.js';
import Tabs from '../../../shared/controls/Tabs.vue';
import AccountAvatar from '../../accounts/components/AccountAvatar.vue';
import { accountsForBusiness } from '../../platforms/lib/catalog.js';
import {
  attendanceAutoEffectiveInterval,
  attendanceAutoPhase,
  attendanceBatchDisplayStatus,
  attendanceCourseFilterSelection,
  attendanceConfigurationDue,
  attendanceKinds,
  attendanceLocation,
  attendanceMaterialLabels,
  attendancePollDelay,
  attendanceRunning,
  attendanceScanFeedback,
  attendanceStates,
} from '../lib/model.js';
import { readCurrentAttendanceLocation } from '../lib/location.js';
import AttendanceAccountRail from './AttendanceAccountRail.vue';

const props = defineProps({ accounts: { type: Array, default: () => [] } });
const emit = defineEmits(['authorize']);
const accounts = computed(() => accountsForBusiness(props.accounts, 'attendance'));
const readyAccounts = computed(() => accounts.value.filter(account => account.status === 'ready'));
const tab = ref('links');
const qrGroups = ref([]);
const autoGroups = ref([]);
const profiles = ref({});
const batches = ref({});
const relay = ref({ configured: false });
const globalLocation = ref(null);
const selection = ref({ type: '', id: '' });
const loading = ref(true);
const refreshing = ref(false);
const error = ref('');
const message = ref('');
const autoFeedback = ref({ kind: '', message: '' });
const busy = ref('');

const linkOpen = ref(false), linkEditingId = ref(''), linkName = ref(''), linkPin = ref(''), linkAccountIds = ref([]);
const linkBusy = ref(false), linkError = ref(''), linkResultUrl = ref(''), linkResultImage = ref('');
const autoOpen = ref(false), autoEditingId = ref(''), autoName = ref(''), autoAccountIds = ref([]);
const autoKinds = ref(['normal', 'photo', 'gesture', 'code', 'location']), autoCourseFilters = ref([]);
const autoWindows = ref([]);
const autoInterval = ref(30), autoEnabled = ref(true);
const autoBusy = ref(false), autoError = ref('');
const autoCourseChoices = ref([]), autoCourseBusy = ref(false), autoCourseFetched = ref(false), autoCourseError = ref('');
const assetOpen = ref(false), assetAccount = ref(null), assetProfile = ref(null), assetBusy = ref(''), assetError = ref('');
const assetInput = ref(null), assetKind = ref('photo');
const locationOpen = ref(false), locationBusy = ref(false), locationLocating = ref(false), locationError = ref(''), locationErrorCode = ref(0);
const locationLatitude = ref(''), locationLongitude = ref(''), locationAddress = ref(''), locationSource = ref('manual');
const selectedLinkImage = ref('');
let timer, disposed = false, configurationLoadedAt = 0;

const tabs = computed(() => [
  { value: 'links', label: '创建签到链接', count: qrGroups.value.filter(group => !group.revoked).length },
  { value: 'auto', label: '自动签到', count: autoGroups.value.length },
]);
const activeLinks = computed(() => qrGroups.value.filter(group => !group.revoked));
const selectedGroup = computed(() => (
  selection.value.type === 'link'
    ? qrGroups.value.find(group => group.id === selection.value.id)
    : selection.value.type === 'auto'
      ? autoGroups.value.find(group => group.id === selection.value.id)
      : null
));
const selectedBatch = computed(() => selectedGroup.value?.last_batch_id ? batches.value[selectedGroup.value.last_batch_id] : null);
const attendancePollActive = computed(() => Boolean(
  attendanceRunning(selectedBatch.value)
  || activeLinks.value.some(group => ['queued', 'running', 'signing'].includes(group.last_status))
  || autoGroups.value.some(group => attendanceRunning(batches.value[group.last_batch_id]))
  || busy.value.startsWith('auto-scan:')
));
const dialogAccounts = computed(() => readyAccounts.value.filter(account => (
  (linkOpen.value && linkAccountIds.value.includes(account.id))
  || (autoOpen.value && autoAccountIds.value.includes(account.id))
)));
const canCreateLink = computed(() => Boolean(linkName.value.trim() && linkAccountIds.value.length && !linkBusy.value));
const autoMissingPhotoNames = computed(() => (
  autoKinds.value.includes('photo')
    ? autoAccountIds.value.filter(identifier => !profiles.value[identifier]?.photo).map(accountName)
    : []
));
const autoMissingLocation = computed(() => autoKinds.value.includes('location') && !globalLocation.value);
const autoMaterialIssue = computed(() => {
  if (autoMissingPhotoNames.value.length) {
    return `拍照签到缺少照片素材：${autoMissingPhotoNames.value.join('、')}`;
  }
  if (autoMissingLocation.value) return '位置签到需要先设置位置签到素材';
  return '';
});
const canSaveAuto = computed(() => Boolean(
  autoName.value.trim()
  && autoAccountIds.value.length
  && autoKinds.value.length
  && !autoMaterialIssue.value
  && autoWindows.value.every(window => (
    window.days.length
    && window.start
    && window.end
    && window.start !== window.end
  ))
  && !autoBusy.value
));
const canSaveLocation = computed(() => Boolean(
  locationLatitude.value !== ''
  && locationLongitude.value !== ''
  && locationAddress.value.trim()
  && !locationBusy.value
  && !locationLocating.value
));
const relayReady = computed(() => Boolean(relay.value?.configured));
const statusLabel = status => attendanceStates[status] || ({
  waiting: '等待扫码', signing: '执行中', completed: '已完成', partial: '部分完成',
  failed: '失败', revoked: '已撤销', running: '执行中',
}[status] || status || '等待扫码');
const groupStatus = group => (
  group?.revoked ? '已撤销'
    : group?.last_status === 'offline' ? '中继断线'
      : group?.last_status === 'completed' ? '上次已完成'
      : group?.last_status === 'partial' ? '上次部分完成'
        : group?.last_status === 'failed' ? '上次需重试'
          : group?.last_status === 'running' ? '执行中'
            : '等待扫码'
);
const autoCooldownValues = group => (
  group?.account_ids?.length
    ? group.account_ids.map(id => Number(group.account_cooldowns?.[id] || 0))
    : [Number(group?.cooldown_until || 0)]
);
const autoCooldown = group => {
  const now = Date.now() / 1000;
  return Math.max(0, Math.ceil(Math.max(0, ...autoCooldownValues(group)) - now));
};
const autoAllCooling = group => {
  const values = autoCooldownValues(group);
  const now = Date.now() / 1000;
  return values.length > 0 && values.every(until => until > now);
};
const autoAnyCooling = group => {
  const now = Date.now() / 1000;
  return autoCooldownValues(group).some(until => until > now);
};
const autoBudgetValues = (group, kind) => (
  group?.account_ids?.map(id => group.account_budgets?.[id]).filter(Boolean)
  || []
).map(budget => ({
  remaining: Number(budget[`${kind}_remaining`] || 0),
  limit: Number(budget[`${kind}_limit`] || 0),
}));
const autoAllBudgetExhausted = (group, kind) => {
  const values = autoBudgetValues(group, kind);
  return values.length > 0 && values.every(budget => budget.remaining <= 0);
};
const autoAnyBudgetExhausted = (group, kind) => (
  autoBudgetValues(group, kind).some(budget => budget.remaining <= 0)
);
const autoClassCooling = group => Number(group?.class_scan?.cooling_classes || 0);
const autoBudgetLabel = (group, kind) => {
  const values = autoBudgetValues(group, kind);
  if (!values.length) return '未统计';
  return `剩余 ${Math.min(...values.map(budget => budget.remaining))} / ${Math.max(...values.map(budget => budget.limit))}`;
};
const weekdayLabels = ['一', '二', '三', '四', '五', '六', '日'];
const autoWindowLabel = group => (
  group?.windows?.length
    ? group.windows.map(window => (
      `${window.days.map(day => `周${weekdayLabels[day - 1]}`).join('、')} ${window.start}-${window.end}`
    )).join('；')
    : '默认（7:00–22:00）'
);
const autoPhase = group => attendanceAutoPhase(group);
const autoLatestBatch = group => group?.last_batch_id ? batches.value[group.last_batch_id] : null;
const autoLatestStatus = group => attendanceBatchDisplayStatus(autoLatestBatch(group));
const autoNeedsAttention = group => ['awaiting_input', 'manual_required', 'auth_required', 'unknown', 'failed'].includes(autoLatestStatus(group));
const autoIntervalLabel = group => {
  const base = Math.max(3, Number(group?.interval_seconds) || 60);
  const effective = attendanceAutoEffectiveInterval(group);
  return effective < base ? `${base} 秒 / 当前 ${effective} 秒` : `${base} 秒`;
};
const autoStatus = group => (
  !group.enabled ? '已关闭'
    : autoAllCooling(group) ? `限流冷却 ${autoCooldown(group)} 秒`
    : autoAllBudgetExhausted(group, 'scan') ? '扫描额度用尽'
    : autoAnyBudgetExhausted(group, 'scan') ? '部分账号额度用尽'
    : autoAnyCooling(group) ? '部分账号冷却'
    : autoClassCooling(group) ? '部分班级冷却'
    : group.last_error ? '上次异常'
    : autoGroupMaterialIssue(group) ? '素材未齐'
      : autoNeedsAttention(group) ? statusLabel(autoLatestStatus(group))
        : autoPhase(group) === 'outside' ? '时段外'
        : autoPhase(group) === 'prewarm' ? '即将检测'
        : autoPhase(group) === 'burst' ? '高频检测'
        : autoLatestBatch(group) ? statusLabel(autoLatestStatus(group))
        : group.last_scan_at ? '未发现可签活动'
          : '等待首次检测'
);
const autoStatusClass = group => (
  busy.value === 'auto-scan:' + group.id ? 'running'
    : !group.enabled ? 'cancelled'
    : autoAllCooling(group) || autoAnyCooling(group) || autoClassCooling(group)
      || autoAllBudgetExhausted(group, 'scan') || group.last_error
      || autoGroupMaterialIssue(group) || autoNeedsAttention(group) ? 'failed'
      : autoPhase(group) === 'outside' ? 'cancelled'
        : 'running'
);
const time = value => value ? new Date(typeof value === 'number' ? value * 1000 : value).toLocaleString('zh-CN', {
  month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
}) : '';
const assetUrl = (accountId, kind, version = '') => api.attendanceAssetUrl(accountId, kind, version || profiles.value[accountId]?.updated_at || '');
const accountName = id => accounts.value.find(account => account.id === id)?.name || '未知账号';
const autoGroupMaterialIssue = group => {
  const photoMissing = group.kinds.includes('photo') && group.account_ids.some(identifier => !profiles.value[identifier]?.photo);
  const locationMissing = group.kinds.includes('location') && !globalLocation.value;
  return photoMissing || locationMissing;
};

async function refresh(forceConfiguration = false) {
  if (refreshing.value) return;
  refreshing.value = true;
  try {
    const loadConfiguration = (
      forceConfiguration === true
      || attendanceConfigurationDue(configurationLoadedAt)
      || readyAccounts.value.some(account => !profiles.value[account.id])
    );
    const configurationPromise = loadConfiguration ? Promise.all([
      api.attendanceLocation().catch(() => globalLocation.value || {}),
      api.attendanceRelay().catch(() => relay.value),
      Promise.all(readyAccounts.value.map(async account => {
        try { return [account.id, await api.attendanceProfile(account.id)]; }
        catch { return [account.id, profiles.value[account.id] || {}]; }
      })),
    ]) : null;
    const [links, autos] = await Promise.all([
      api.attendanceQrGroups().catch(() => qrGroups.value),
      api.attendanceAutoGroups().catch(() => autoGroups.value),
    ]);
    const configuration = configurationPromise ? await configurationPromise : null;
    if (disposed) return;
    qrGroups.value = links;
    autoGroups.value = autos;
    if (configuration) {
      const [location, relayStatus, profileEntries] = configuration;
      globalLocation.value = location?.address ? location : null;
      relay.value = relayStatus;
      profiles.value = Object.fromEntries(profileEntries);
      configurationLoadedAt = Date.now();
    }
    const selectedExists = selectedGroup.value;
    if (!selectedExists) {
      const link = links.find(group => !group.revoked);
      const auto = autos[0];
      const preferred = tab.value === 'auto'
        ? (auto ? { type: 'auto', id: auto.id } : link ? { type: 'link', id: link.id } : null)
        : (link ? { type: 'link', id: link.id } : auto ? { type: 'auto', id: auto.id } : null);
      selection.value = preferred || { type: '', id: '' };
    }
    await loadSelectedBatch(true);
    error.value = '';
  } catch (cause) {
    error.value = cause.message;
  } finally {
    loading.value = false;
    refreshing.value = false;
  }
}

function scheduleRefresh() {
  clearTimeout(timer);
  if (disposed) return;
  const delay = attendancePollDelay(attendancePollActive.value);
  timer = setTimeout(async () => { await refresh(false); scheduleRefresh(); }, delay);
}

async function loadSelectedBatch(force = false) {
  const identifier = selectedGroup.value?.last_batch_id;
  if (!identifier || (batches.value[identifier] && !force)) return batches.value[identifier];
  try {
    const batch = await api.attendanceBatch(identifier);
    if (!disposed) batches.value = { ...batches.value, [identifier]: batch };
    return batch;
  } catch { /* Historical batches may have been removed. */ }
}

function selectGroup(type, id) {
  selection.value = { type, id };
  loadSelectedBatch(true);
}

watch(tab, value => {
  const type = value === 'auto' ? 'auto' : 'link';
  if (selection.value.type === type && selectedGroup.value) return;
  const group = value === 'auto' ? autoGroups.value[0] : activeLinks.value[0];
  if (group) selectGroup(type, group.id);
  else {
    selection.value = { type: '', id: '' };
  }
});

function openLinkDialog(group = null) {
  linkEditingId.value = group?.id || '';
  linkName.value = group?.name || '';
  linkPin.value = '';
  linkAccountIds.value = group ? [...group.account_ids] : [];
  linkResultUrl.value = '';
  linkResultImage.value = '';
  linkError.value = '';
  linkOpen.value = true;
  nextTick(() => document.querySelector('.attendance-dialog input')?.focus());
}

async function saveLink() {
  if (!canCreateLink.value && !linkEditingId.value) return;
  linkBusy.value = true; linkError.value = '';
  try {
    if (linkEditingId.value) {
      const result = await api.renameAttendanceQrGroup(linkEditingId.value, linkName.value.trim());
      qrGroups.value = qrGroups.value.map(group => group.id === result.id ? result : group);
      message.value = '链接已重命名';
      linkOpen.value = false;
      return;
    }
    const result = await api.createAttendanceQrGroup({
      name: linkName.value.trim(), account_ids: linkAccountIds.value, pin: linkPin.value.trim(), base_url: '',
    });
    qrGroups.value = [result, ...qrGroups.value.filter(group => group.id !== result.id)];
    selection.value = { type: 'link', id: result.id };
    linkResultUrl.value = result.url;
    linkResultImage.value = await QRCode.toDataURL(result.url, { width: 220, margin: 2 });
    message.value = '扫码链接已创建';
  } catch (cause) { linkError.value = cause.message; }
  finally { linkBusy.value = false; }
}

async function copy(value) {
  try { await navigator.clipboard.writeText(value); message.value = '链接已复制'; }
  catch { error.value = '未能复制，请选中链接手动复制'; }
}

async function revokeLink(group) {
  if (!window.confirm(`撤销“${group.name}”？撤销后不能继续扫码。`)) return;
  busy.value = 'link-revoke:' + group.id;
  try {
    await api.revokeAttendanceQrGroup(group.id);
    qrGroups.value = qrGroups.value.map(item => item.id === group.id ? { ...item, revoked: true, last_status: 'revoked' } : item);
    selection.value = activeLinks.value[0] ? { type: 'link', id: activeLinks.value[0].id } : { type: '', id: '' };
    message.value = '扫码链接已撤销';
  } catch (cause) { error.value = cause.message; }
  finally { busy.value = ''; }
}

function openAutoDialog(group = null) {
  autoEditingId.value = group?.id || '';
  autoName.value = group?.name || '';
  autoAccountIds.value = group ? [...group.account_ids] : [];
  autoKinds.value = group ? [...group.kinds] : ['normal', 'photo', 'gesture', 'code', 'location'];
  autoCourseFilters.value = group ? [...(group.course_filters || (group.course_filter ? [group.course_filter] : []))] : [];
  autoWindows.value = group?.windows?.map(window => ({ ...window, days: [...window.days] })) || [];
  autoInterval.value = group?.interval_seconds || 30;
  autoEnabled.value = group?.enabled ?? true;
  autoError.value = '';
  autoCourseChoices.value = [];
  autoCourseFetched.value = false;
  autoCourseError.value = '';
  autoOpen.value = true;
}

function addAutoWindow() {
  autoWindows.value.push({ days: [1, 2, 3, 4, 5], start: '08:00', end: '18:00' });
}

function removeAutoWindow(index) {
  autoWindows.value.splice(index, 1);
}

function resetAutoCourseFetch() {
  autoCourseChoices.value = [];
  autoCourseFilters.value = [];
  autoCourseFetched.value = false;
  autoCourseError.value = '';
}

async function fetchAutoCourses() {
  if (!autoAccountIds.value.length) {
    autoCourseError.value = '请先选择账号';
    return;
  }
  autoCourseBusy.value = true;
  autoCourseError.value = '';
  try {
    const result = await api.attendanceAutoGroupCourses(autoAccountIds.value);
    autoCourseChoices.value = [...result.courses].sort((left, right) => (
      Number(right.common) - Number(left.common)
      || left.name.localeCompare(right.name, 'zh-CN')
    ));
    autoCourseFilters.value = attendanceCourseFilterSelection(
      autoCourseFilters.value,
      autoCourseChoices.value,
      Boolean(result.errors?.length),
    );
    autoCourseFetched.value = true;
    if (result.errors?.length) {
      autoCourseError.value = result.errors.map(error => (
        `${accountName(error.account_id)}：${error.message}`
      )).join('；');
    }
  } catch (cause) {
    autoCourseChoices.value = [];
    autoCourseFetched.value = false;
    autoCourseError.value = cause.message;
  } finally {
    autoCourseBusy.value = false;
  }
}

async function saveAutoGroup() {
  if (!autoName.value.trim() || !autoAccountIds.value.length || !autoKinds.value.length || autoBusy.value) return;
  if (autoMaterialIssue.value) {
    autoError.value = autoMaterialIssue.value;
    return;
  }
  autoBusy.value = true; autoError.value = '';
  try {
    const payload = {
      name: autoName.value.trim(), account_ids: autoAccountIds.value, kinds: autoKinds.value,
      course_filters: autoCourseFilters.value, windows: autoWindows.value,
      interval_seconds: autoInterval.value,
      enabled: autoEnabled.value, location: null,
    };
    const result = autoEditingId.value
      ? await api.updateAttendanceAutoGroup(autoEditingId.value, payload)
      : await api.createAttendanceAutoGroup(payload);
    autoGroups.value = autoEditingId.value
      ? autoGroups.value.map(group => group.id === result.id ? result : group)
      : [result, ...autoGroups.value];
    selection.value = { type: 'auto', id: result.id };
    autoOpen.value = false;
    message.value = autoEditingId.value ? '自动签到分组已更新' : '自动签到分组已创建';
  } catch (cause) { autoError.value = cause.message; }
  finally { autoBusy.value = false; }
}

async function scanAuto(group) {
  busy.value = 'auto-scan:' + group.id;
  error.value = '';
  autoFeedback.value = { kind: '', message: '' };
  try {
    const result = await api.scanAttendanceAutoGroup(group.id);
    autoGroups.value = autoGroups.value.map(item => item.id === group.id ? result : item);
    const batch = result.last_batch_id
      ? await api.attendanceBatch(result.last_batch_id).catch(() => null)
      : null;
    if (batch) batches.value = { ...batches.value, [batch.id]: batch };
    autoFeedback.value = attendanceScanFeedback(batch, result.last_error);
  } catch (cause) {
    const message = cause.message === 'Not Found'
      ? '本地服务尚未加载“立即检测”接口，请重启本地服务后的最新版本再试。'
      : cause.message;
    autoFeedback.value = { kind: 'error', message };
  }
  finally { busy.value = ''; }
}

async function deleteAuto(group) {
  if (!window.confirm(`删除自动签到分组“${group.name}”？`)) return;
  try {
    await api.deleteAttendanceAutoGroup(group.id);
    autoGroups.value = autoGroups.value.filter(item => item.id !== group.id);
    selection.value = autoGroups.value[0] ? { type: 'auto', id: autoGroups.value[0].id } : { type: '', id: '' };
    message.value = '自动签到分组已删除';
  } catch (cause) { error.value = cause.message; }
}

async function openAssets(account) {
  assetAccount.value = account;
  assetProfile.value = await api.attendanceProfile(account.id).catch(cause => {
    assetError.value = cause.message; return {};
  });
  assetError.value = '';
  assetOpen.value = true;
}

function chooseAsset(kind) {
  assetKind.value = kind;
  assetInput.value?.click();
}

async function uploadAsset(event) {
  const file = event.target.files?.[0];
  event.target.value = '';
  if (!file || !assetAccount.value) return;
  assetBusy.value = assetKind.value;
  assetError.value = '';
  try {
    const result = await api.uploadAttendanceAsset(assetAccount.value.id, assetKind.value, file);
    assetProfile.value = result;
    profiles.value = { ...profiles.value, [assetAccount.value.id]: result };
    message.value = assetKind.value === 'photo' ? '照片素材已保存' : '人脸素材已保存';
  } catch (cause) { assetError.value = cause.message; }
  finally { assetBusy.value = ''; }
}

async function deleteAsset(kind) {
  if (!assetAccount.value) return;
  assetBusy.value = kind;
  try {
    assetProfile.value = await api.deleteAttendanceAsset(assetAccount.value.id, kind);
    profiles.value = { ...profiles.value, [assetAccount.value.id]: assetProfile.value };
  } catch (cause) { assetError.value = cause.message; }
  finally { assetBusy.value = ''; }
}

function openLocationDialog() {
  locationLatitude.value = globalLocation.value?.latitude ?? '';
  locationLongitude.value = globalLocation.value?.longitude ?? '';
  locationAddress.value = globalLocation.value?.address || '';
  locationSource.value = globalLocation.value?.source || 'manual';
  locationLocating.value = false;
  locationError.value = '';
  locationErrorCode.value = 0;
  locationOpen.value = true;
}

async function getCurrentLocation() {
  locationLocating.value = true;
  locationError.value = '';
  locationErrorCode.value = 0;
  try {
    const location = await readCurrentAttendanceLocation();
    locationLatitude.value = location.latitude;
    locationLongitude.value = location.longitude;
    locationSource.value = location.source;
    if (!locationAddress.value.trim()) locationAddress.value = '当前位置';
  } catch (cause) {
    locationErrorCode.value = cause.code || 2;
    locationError.value = cause.message || '未能获取当前位置，请检查定位设置后重试';
  } finally {
    locationLocating.value = false;
  }
}

async function openLocationSettings() {
  if (!window.desktopApi?.openLocationSettings) {
    locationError.value = '请从 Mumu 桌面版打开 Windows 定位设置';
    return;
  }
  try {
    await window.desktopApi.openLocationSettings();
    locationError.value = '已打开 Windows 定位设置，请允许 Mumu 使用位置后再重试';
  } catch (cause) {
    locationError.value = cause.message;
  }
}

async function saveLocation() {
  locationBusy.value = true; locationError.value = '';
  try {
    const location = attendanceLocation(
      locationLatitude.value, locationLongitude.value, locationAddress.value, locationSource.value,
    );
    if (!location) throw new Error('请先获取当前位置');
    globalLocation.value = await api.saveAttendanceLocation({
      latitude: location.latitude, longitude: location.longitude, address: location.address,
    });
    locationOpen.value = false;
    message.value = '位置签到素材已保存';
  } catch (cause) { locationError.value = cause.message; }
  finally { locationBusy.value = false; }
}

async function clearLocation() {
  try {
    await api.deleteAttendanceLocation();
    globalLocation.value = null;
    locationOpen.value = false;
    message.value = '位置签到素材已清除';
  } catch (cause) { locationError.value = cause.message; }
}

watch(() => [selectedGroup.value?.id, selectedGroup.value?.url], async ([identifier, url]) => {
  selectedLinkImage.value = '';
  if (url) {
    try { selectedLinkImage.value = await QRCode.toDataURL(url, { width: 220, margin: 2 }); }
    catch { selectedLinkImage.value = ''; }
  }
  if (identifier) loadSelectedBatch(true);
});

onMounted(() => { refresh(true).finally(scheduleRefresh); });
onUnmounted(() => { disposed = true; clearTimeout(timer); });
</script>

<template>
  <main class="attendance-workspace">
    <AttendanceAccountRail
      :accounts="accounts" :notes="{}" :qr-groups="qrGroups" :auto-groups="autoGroups" :profiles="profiles"
      @configure-assets="openAssets" @authorize="emit('authorize')"
    />

    <section class="attendance-center attendance-config-center">
      <header class="attendance-page-heading">
        <div><h1>配置签到</h1></div>
        <button class="button dark" type="button" :disabled="!readyAccounts.length" @click="tab === 'links' ? openLinkDialog() : openAutoDialog()">
          <Link v-if="tab === 'links'" :size="15" /><Clock3 v-else :size="15" />
          {{ tab === 'links' ? '创建链接' : '创建分组' }}
        </button>
      </header>

      <Tabs v-model="tab" :items="tabs" label="签到配置类型" />

      <p v-if="error" class="attendance-feedback error" role="alert"><CircleAlert :size="15" />{{ error }}</p>
      <p v-if="message" class="attendance-feedback success" role="status"><CheckCheck :size="15" />{{ message }}</p>
      <p v-if="autoFeedback.message" class="attendance-feedback" :class="autoFeedback.kind" role="status"><CircleAlert v-if="autoFeedback.kind === 'error'" :size="15" /><CheckCheck v-else :size="15" />{{ autoFeedback.message }}</p>
      <div v-if="loading" class="attendance-config-loading"><LoaderCircle :size="18" class="spin" />正在读取签到配置</div>

      <template v-else-if="tab === 'links'">
        <div v-if="!relayReady" class="attendance-config-warning"><CircleAlert :size="16" /><span>尚未配置扫码域名，请先在“设置 > 签到域名”中连接中继服务。</span></div>
        <section class="attendance-config-section">
          <header><div><h2>扫码链接</h2><span>{{ activeLinks.length }} 个可用</span></div></header>
          <div class="attendance-config-list">
            <article v-for="group in activeLinks" :key="group.id" :class="{ active: selection.type === 'link' && selection.id === group.id }" @click="selectGroup('link', group.id)">
              <span class="attendance-config-icon"><Link :size="17" /></span>
              <div><strong>{{ group.name }}</strong><small>{{ group.participants.map(account => account.name).join('、') }}</small></div>
              <span class="attendance-status" :class="group.last_status">{{ groupStatus(group) }}</span>
              <button class="icon-button" type="button" title="复制链接" aria-label="复制签到链接" @click.stop="copy(group.url)"><Copy :size="15" /></button>
              <button class="icon-button" type="button" title="重命名" aria-label="重命名签到链接" @click.stop="openLinkDialog(group)"><Pencil :size="15" /></button>
              <button class="icon-button danger-text" type="button" title="撤销" aria-label="撤销签到链接" @click.stop="revokeLink(group)"><Trash2 :size="15" /></button>
            </article>
            <div v-if="!activeLinks.length" class="attendance-config-empty"><Link :size="28" /><strong>还没有扫码链接</strong><button class="button light" type="button" :disabled="!readyAccounts.length" @click="openLinkDialog()">创建链接</button></div>
          </div>
        </section>
      </template>

      <template v-else>
        <section class="attendance-location-strip">
          <span class="attendance-config-icon"><MapPin :size="17" /></span>
          <div><strong>位置签到素材</strong><small>{{ globalLocation?.address || '未设置' }}</small></div>
          <button class="button light compact" type="button" @click="openLocationDialog"><Pencil :size="13" />{{ globalLocation ? '修改' : '设置' }}</button>
        </section>
        <section class="attendance-config-section">
          <header><div><h2>自动签到分组</h2><span>{{ autoGroups.length }} 个分组</span></div></header>
          <div class="attendance-config-list">
            <article v-for="group in autoGroups" :key="group.id" :class="{ active: selection.type === 'auto' && selection.id === group.id }" @click="selectGroup('auto', group.id)">
              <span class="attendance-config-icon"><Clock3 :size="17" /></span>
              <div><strong>{{ group.name }}</strong><small>{{ group.participants.map(account => account.name).join('、') }}</small></div>
              <span class="attendance-status" :class="autoStatusClass(group)">{{ busy === 'auto-scan:' + group.id ? '检测中' : autoStatus(group) }}</span>
              <button class="icon-button" type="button" :title="autoAllCooling(group) ? '所有账号限流冷却中' : autoAllBudgetExhausted(group, 'scan') ? '所有账号扫描额度已用完' : '立即检测'" aria-label="立即检测自动签到分组" :disabled="autoAllCooling(group) || autoAllBudgetExhausted(group, 'scan') || busy === 'auto-scan:' + group.id" @click.stop="scanAuto(group)"><LoaderCircle v-if="busy === 'auto-scan:' + group.id" :size="15" class="spin" /><RefreshCw v-else :size="15" /></button>
              <button class="icon-button" type="button" title="编辑" aria-label="编辑自动签到分组" @click.stop="openAutoDialog(group)"><Pencil :size="15" /></button>
              <button class="icon-button danger-text" type="button" title="删除" aria-label="删除自动签到分组" @click.stop="deleteAuto(group)"><Trash2 :size="15" /></button>
            </article>
            <div v-if="!autoGroups.length" class="attendance-config-empty"><Clock3 :size="28" /><strong>还没有自动签到分组</strong><button class="button light" type="button" :disabled="!readyAccounts.length" @click="openAutoDialog()">创建分组</button></div>
          </div>
        </section>
      </template>
    </section>

    <aside class="attendance-run-panel attendance-config-detail">
      <template v-if="selectedGroup">
        <header class="attendance-run-heading">
          <div><span class="panel-label"><component :is="selection.type === 'link' ? Link : Clock3" :size="15" />{{ selection.type === 'link' ? '扫码链接' : '自动签到' }}</span><h2>{{ selectedGroup.name }}</h2></div>
          <span class="attendance-status" :class="selection.type === 'link' ? selectedGroup.last_status : autoStatusClass(selectedGroup)">{{ selection.type === 'link' ? groupStatus(selectedGroup) : busy === 'auto-scan:' + selectedGroup.id ? '检测中' : autoStatus(selectedGroup) }}</span>
        </header>

        <section class="attendance-detail-block">
          <h3><Users :size="14" />账号</h3>
          <div class="attendance-detail-members"><span v-for="account in selectedGroup.participants" :key="account.id">{{ account.name }}</span></div>
        </section>

        <section v-if="selection.type === 'link'" class="attendance-detail-block">
          <h3><Link :size="14" />链接</h3>
          <img v-if="selectedLinkImage" class="attendance-detail-qr" :src="selectedLinkImage" alt="签到链接二维码">
          <div class="attendance-detail-url"><input :value="selectedGroup.url" readonly aria-label="签到链接"><button class="button light compact" type="button" @click="copy(selectedGroup.url)"><Copy :size="13" />复制</button></div>
        </section>

        <section v-else class="attendance-detail-block">
          <h3><Clock3 :size="14" />自动设置</h3>
          <dl class="attendance-detail-facts">
            <div><dt>检测间隔</dt><dd>{{ autoIntervalLabel(selectedGroup) }}</dd></div>
            <div><dt>处理类型</dt><dd>{{ selectedGroup.kinds.map(kind => attendanceKinds[kind]).join('、') }}</dd></div>
            <div><dt>检测课程</dt><dd>{{ selectedGroup.course_filters?.length ? selectedGroup.course_filters.join('、') : '当年全部课程' }}</dd></div>
            <div><dt>检测时段</dt><dd>{{ autoWindowLabel(selectedGroup) }}</dd></div>
            <div><dt>班级扫描</dt><dd>{{ selectedGroup.class_scan?.classes ? `${selectedGroup.class_scan.classes} 个班级，复用 ${selectedGroup.class_scan.reused} 次${selectedGroup.class_scan.anchor_switches ? `，切换锚点 ${selectedGroup.class_scan.anchor_switches} 次` : ''}${autoClassCooling(selectedGroup) ? `，冷却 ${autoClassCooling(selectedGroup)} 个` : ''}` : '尚未检测' }}</dd></div>
            <div><dt>扫描额度</dt><dd>{{ autoBudgetLabel(selectedGroup, 'scan') }}</dd></div>
            <div><dt>签到额度</dt><dd>{{ autoBudgetLabel(selectedGroup, 'sign') }}</dd></div>
            <div><dt>位置签到素材</dt><dd>{{ selectedGroup.kinds.includes('location') ? (globalLocation?.address || '未设置') : '未启用' }}</dd></div>
            <div><dt>上次检测</dt><dd>{{ time(selectedGroup.last_scan_at) || '尚未检测' }}</dd></div>
          </dl>
          <p v-if="autoGroupMaterialIssue(selectedGroup)" class="attendance-material-alert"><CircleAlert :size="14" />当前分组所需素材不完整，执行前请补齐。</p>
          <p v-if="selectedGroup.last_error" class="attendance-feedback error"><CircleAlert :size="14" />{{ selectedGroup.last_error }}</p>
        </section>

        <section class="attendance-detail-block attendance-detail-results">
          <header><h3><ScanFace :size="14" />最近结果</h3><button class="icon-button" type="button" title="刷新" @click="refresh(true)"><RefreshCw :size="14" /></button></header>
          <template v-if="selectedBatch">
            <div class="attendance-run-summary"><div><strong>{{ selectedBatch.completed }} / {{ selectedBatch.total }}</strong><span>{{ statusLabel(selectedBatch.status) }}</span></div><p>{{ time(selectedBatch.created_at) }}</p></div>
            <article v-for="item in selectedBatch.items" :key="item.id" :data-status="item.status">
              <div><strong>{{ item.account_name }}</strong><span>{{ item.course_name }} · {{ item.title }}</span></div>
              <span class="attendance-status" :class="item.status">{{ statusLabel(item.status) }}</span>
              <p>{{ item.message }}</p>
            </article>
          </template>
          <div v-else class="attendance-detail-empty"><RefreshCw :size="23" /><span>还没有执行结果</span></div>
        </section>
      </template>
      <div v-else class="attendance-run-empty"><Link :size="28" /><strong>选择一个签到分组</strong></div>
    </aside>

    <Transition name="dialog">
      <div v-if="linkOpen" class="modal-backdrop" @mousedown.self="linkOpen = false">
        <section class="modal-card attendance-dialog" role="dialog" aria-modal="true" aria-label="签到链接设置">
          <div class="modal-heading"><div><h2>{{ linkEditingId ? '重命名签到链接' : '创建签到链接' }}</h2></div><button class="icon-button" type="button" aria-label="关闭" @click="linkOpen = false"><X :size="17" /></button></div>
          <div class="attendance-dialog-body">
            <label class="field-label"><span>链接名称</span><input v-model="linkName" maxlength="80" placeholder="例如：计算机科学 1 组"></label>
            <label v-if="!linkEditingId" class="field-label"><span>PIN（可选）</span><input v-model="linkPin" inputmode="numeric" maxlength="6" placeholder="4 至 6 位数字"></label>
            <section class="attendance-account-picker">
              <header><strong>选择账号</strong><span>{{ linkAccountIds.length }} 个已选</span></header>
              <label v-for="account in readyAccounts" :key="account.id"><ChoiceInput v-model="linkAccountIds" :value="account.id" :disabled="Boolean(linkEditingId)" /><AccountAvatar :account="account" :size="28" /><span>{{ account.name }}</span></label>
            </section>
            <section v-if="linkResultUrl" class="attendance-share-result"><img :src="linkResultImage" alt="签到链接二维码"><div><strong>链接已创建</strong><input :value="linkResultUrl" readonly aria-label="签到链接"><button class="button light compact" type="button" @click="copy(linkResultUrl)"><Copy :size="13" />复制</button></div></section>
            <p v-if="linkError" class="attendance-feedback error"><CircleAlert :size="15" />{{ linkError }}</p>
          </div>
          <div class="modal-actions"><button class="button light" type="button" @click="linkOpen = false">关闭</button><button v-if="!linkResultUrl" class="button dark" type="button" :disabled="!canCreateLink" @click="saveLink"><LoaderCircle v-if="linkBusy" :size="14" class="spin" /><Link v-else :size="14" />{{ linkEditingId ? '保存名称' : '创建链接' }}</button></div>
        </section>
      </div>
    </Transition>

    <Transition name="dialog">
      <div v-if="autoOpen" class="modal-backdrop" @mousedown.self="autoOpen = false">
        <section class="modal-card attendance-dialog" role="dialog" aria-modal="true" aria-label="自动签到分组设置">
          <div class="modal-heading"><div><h2>{{ autoEditingId ? '编辑自动签到分组' : '创建自动签到分组' }}</h2></div><button class="icon-button" type="button" aria-label="关闭" @click="autoOpen = false"><X :size="17" /></button></div>
          <div class="attendance-dialog-body">
            <label class="field-label"><span>分组名称</span><input v-model="autoName" maxlength="80" placeholder="例如：计算机科学自动签到"></label>
            <label class="field-label"><span>检测间隔</span><select v-model.number="autoInterval"><option :value="30">30 秒</option><option :value="60">1 分钟</option><option :value="120">2 分钟</option><option :value="300">5 分钟</option></select></label>
            <section class="attendance-window-picker">
              <header><div><strong>检测时段</strong><span>{{ autoWindows.length ? `${autoWindows.length} 个时段 · 开始后 3 分钟提速` : '默认 7:00–22:00' }}</span></div><button class="button light compact" type="button" @click="addAutoWindow"><Plus :size="13" />添加时段</button></header>
              <div v-for="(window, index) in autoWindows" :key="index" class="attendance-window-row">
                <div class="attendance-weekdays"><label v-for="(label, dayIndex) in weekdayLabels" :key="label" :class="{ selected: window.days.includes(dayIndex + 1) }"><ChoiceInput v-model="window.days" :value="dayIndex + 1" /><span>{{ label }}</span></label></div>
                <div class="attendance-window-time"><input v-model="window.start" type="time"><span>至</span><input v-model="window.end" type="time"><button class="icon-button danger-text" type="button" :aria-label="`删除第 ${index + 1} 个检测时段`" @click="removeAutoWindow(index)"><X :size="14" /></button></div>
              </div>
            </section>
            <section class="attendance-account-picker"><header><strong>选择账号</strong><span>{{ autoAccountIds.length }} 个已选</span></header><label v-for="account in readyAccounts" :key="account.id"><ChoiceInput v-model="autoAccountIds" :value="account.id" @change="resetAutoCourseFetch" /><AccountAvatar :account="account" :size="28" /><span>{{ account.name }}</span></label></section>
            <section class="attendance-course-picker">
              <header><div><strong>检测课程</strong><span>{{ autoCourseFilters.length ? `已选 ${autoCourseFilters.length} 门` : '未选择时检测当年全部课程' }}</span></div><button class="button light compact" type="button" :disabled="!autoAccountIds.length || autoCourseBusy" @click="fetchAutoCourses"><LoaderCircle v-if="autoCourseBusy" :size="13" class="spin" /><RefreshCw v-else :size="13" />{{ autoCourseBusy ? '获取中' : '获取课程' }}</button></header>
              <div v-if="autoCourseChoices.length" class="attendance-course-options">
                <label v-for="course in autoCourseChoices" :key="course.name"><ChoiceInput v-model="autoCourseFilters" :value="course.name" /><span>{{ course.name }}</span><small>{{ course.common ? '全部账号' : `${course.account_ids.length}/${autoAccountIds.length} 个账号` }}</small></label>
              </div>
              <p v-else class="attendance-course-empty">{{ autoCourseFetched ? '所选账号没有可检测课程' : '选择账号后获取当年课程' }}</p>
              <p v-if="autoCourseError" class="attendance-feedback error"><CircleAlert :size="14" />{{ autoCourseError }}</p>
            </section>
            <fieldset class="attendance-type-picker"><legend>处理类型</legend><label v-for="(label, kind) in { normal: '普通', photo: '拍照', gesture: '手势码', code: '签到码', location: '位置' }" :key="kind"><ChoiceInput v-model="autoKinds" :value="kind" /><span><strong>{{ label }}</strong><small>{{ attendanceMaterialLabels[kind] }}</small></span></label></fieldset>
            <p v-if="autoMaterialIssue" class="attendance-material-alert"><CircleAlert :size="14" /><span>{{ autoMaterialIssue }}</span><button v-if="autoMissingLocation" class="button light compact" type="button" @click="openLocationDialog">设置位置素材</button></p>
            <label class="preference-row"><span><strong>启用分组</strong></span><ToggleSwitch v-model="autoEnabled" role="switch" aria-label="启用自动签到分组" /></label>
            <p v-if="autoError" class="attendance-feedback error"><CircleAlert :size="15" />{{ autoError }}</p>
          </div>
          <div class="modal-actions"><button class="button light" type="button" @click="autoOpen = false">取消</button><button class="button dark" type="button" :disabled="!canSaveAuto" @click="saveAutoGroup"><LoaderCircle v-if="autoBusy" :size="14" class="spin" /><Clock3 v-else :size="14" />保存分组</button></div>
        </section>
      </div>
    </Transition>

    <Transition name="dialog">
      <div v-if="assetOpen" class="modal-backdrop" @mousedown.self="assetOpen = false">
        <section class="modal-card attendance-dialog" role="dialog" aria-modal="true" aria-label="账号签到素材设置">
          <div class="modal-heading"><div><h2>账号签到素材</h2><p>{{ assetAccount?.name }}</p></div><button class="icon-button" type="button" aria-label="关闭" @click="assetOpen = false"><X :size="17" /></button></div>
          <input ref="assetInput" type="file" accept="image/png,image/jpeg,image/webp" hidden @change="uploadAsset">
          <div class="attendance-dialog-body">
            <section class="attendance-asset-row"><div><strong>拍照签到素材</strong></div><img v-if="assetProfile?.photo" :src="assetUrl(assetAccount.id, 'photo')" alt="拍照签到素材"><span v-else class="attendance-asset-placeholder"><Image :size="22" />未设置</span><button class="button light compact" type="button" :disabled="Boolean(assetBusy)" @click="chooseAsset('photo')"><Upload :size="13" />{{ assetProfile?.photo ? '替换' : '上传' }}</button><button v-if="assetProfile?.photo" class="icon-button danger-text" type="button" aria-label="删除拍照签到素材" @click="deleteAsset('photo')"><Trash2 :size="14" /></button></section>
            <section class="attendance-asset-row"><div><strong>人脸校验素材</strong></div><img v-if="assetProfile?.face" :src="assetUrl(assetAccount.id, 'face')" alt="人脸校验素材"><span v-else class="attendance-asset-placeholder"><ScanFace :size="22" />未设置</span><button class="button light compact" type="button" :disabled="Boolean(assetBusy)" @click="chooseAsset('face')"><Upload :size="13" />{{ assetProfile?.face ? '替换' : '上传' }}</button><button v-if="assetProfile?.face" class="icon-button danger-text" type="button" aria-label="删除人脸校验素材" @click="deleteAsset('face')"><Trash2 :size="14" /></button></section>
            <p v-if="assetError" class="attendance-feedback error"><CircleAlert :size="15" />{{ assetError }}</p>
          </div>
        </section>
      </div>
    </Transition>

    <Transition name="dialog">
      <div v-if="locationOpen" class="modal-backdrop" @mousedown.self="locationOpen = false">
        <section class="modal-card attendance-dialog" role="dialog" aria-modal="true" aria-label="位置签到素材">
          <div class="modal-heading"><div><h2>位置签到素材</h2></div><button class="icon-button" type="button" aria-label="关闭" @click="locationOpen = false"><X :size="17" /></button></div>
          <div class="attendance-dialog-body">
            <section class="attendance-location-picker">
              <button class="button light" type="button" :disabled="locationLocating || locationBusy" @click="getCurrentLocation"><LoaderCircle v-if="locationLocating" :size="14" class="spin" /><MapPin v-else :size="14" />{{ locationLocating ? '正在定位' : locationLatitude !== '' ? '重新定位' : '获取当前位置' }}</button>
              <div v-if="locationLatitude !== '' && locationLongitude !== ''" class="attendance-location-current"><MapPin :size="17" /><div><strong>{{ locationAddress || '当前位置' }}</strong><small>{{ Number(locationLatitude).toFixed(6) }}, {{ Number(locationLongitude).toFixed(6) }}</small></div></div>
            </section>
            <label class="field-label"><span>地点名称</span><input v-model="locationAddress" maxlength="200" placeholder="例如：教学楼 A 区"></label>
            <p v-if="locationError" class="attendance-feedback error"><CircleAlert :size="15" /><span>{{ locationError }}</span><button v-if="locationErrorCode" class="button light compact" type="button" @click="openLocationSettings">打开定位设置</button></p>
          </div>
          <div class="modal-actions"><button v-if="globalLocation" class="button light" type="button" @click="clearLocation"><Trash2 :size="14" />清除</button><button class="button light" type="button" @click="locationOpen = false">取消</button><button class="button dark" type="button" :disabled="!canSaveLocation" @click="saveLocation"><LoaderCircle v-if="locationBusy" :size="14" class="spin" /><MapPin v-else :size="14" />保存位置</button></div>
        </section>
      </div>
    </Transition>
  </main>
</template>

<style src="../../../styles/attendance.css"></style>
