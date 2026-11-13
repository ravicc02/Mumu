<script setup>
import { computed, ref, watch } from "vue";
import { ArrowUpRight, BookOpen, Check, ClipboardList, Ellipsis, Layers3, LoaderCircle, LogIn, Search, Trash2 } from "@lucide/vue";
import PlatformLogo from "./PlatformLogo.vue";
import AccountAvatar from "../../accounts/components/AccountAvatar.vue";
import PlatformCapabilities from "./PlatformCapabilities.vue";
import { supportsBusiness } from "../lib/catalog.js";
import Tabs from "../../../shared/controls/Tabs.vue";

const props = defineProps({
  platforms: { type: Array, default: () => [] }, accounts: { type: Array, default: () => [] },
  modelValue: { type: String, default: "chaoxing" }, selectedAccountId: { type: String, default: "" },
  menuId: { type: String, default: "" }, loginAccountId: { type: String, default: "" },
  isBusy: { type: Function, default: () => false },
});
const emit = defineEmits(["update:modelValue", "add", "authorize", "delete", "menu", "menu-keydown", "assignments", "learning", "confirm-authorization"]);
const query = ref(""), filter = ref("all");
const platform = computed(() => props.platforms.find(item => item.name === props.modelValue));
const records = computed(() => props.accounts.filter(item => item.platform === props.modelValue));
const tabs = computed(() => [
  { value: "all", label: "全部", count: records.value.length },
  { value: "ready", label: "已授权", count: records.value.filter(item => item.status === "ready").length },
  { value: "pending", label: "待授权", count: records.value.filter(item => item.status !== "ready").length },
]);
const filtered = computed(() => records.value.filter(item => item.name.toLocaleLowerCase().includes(query.value.trim().toLocaleLowerCase())
  && (filter.value === "all" || (filter.value === "ready" ? item.status === "ready" : item.status !== "ready"))));
const label = status => ({ ready: "已授权", new: "待授权", authorizing: "授权中", waiting: "等待登录", login_error: "授权失效" })[status] || "待授权";
const authorizing = account => props.loginAccountId === account.id || ["authorizing", "waiting"].includes(account.status);
const date = value => value ? new Date(value).toLocaleString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }) : "尚未授权";
watch(() => props.modelValue, () => { query.value = ""; filter.value = "all"; });
</script>

<template>
  <main class="platform-management">
    <aside class="platform-catalog" aria-label="选择平台">
      <div class="section-title"><h2>平台</h2><span class="count">{{ platforms.length }}</span></div>
      <button v-for="item in platforms" :key="item.name" class="platform-chip" :class="{ active: modelValue === item.name }" type="button" :aria-pressed="modelValue === item.name" @click="emit('update:modelValue', item.name)">
        <PlatformLogo :platform="item.name" :size="38" /><span><strong>{{ item.label }}</strong><PlatformCapabilities :platform="item" /><small>{{ accounts.filter(account => account.platform === item.name).length }} 个账号</small></span><Check v-if="modelValue === item.name" :size="14" />
      </button>
    </aside>
    <section class="platform-accounts">
      <header class="platform-accounts-heading"><div><PlatformLogo :platform="modelValue" :size="34" /><h1>{{ platform?.label || '平台' }}</h1><PlatformCapabilities v-if="platform" :platform="platform" /></div><button class="button dark" type="button" :disabled="!platform" @click="emit('add', modelValue)"><LogIn :size="15" />授权</button></header>
      <div class="platform-account-tools"><h2>授权记录 <span class="count">{{ records.length }}</span></h2><div><Tabs v-model="filter" :items="tabs" label="授权状态" /><label class="search-field"><Search :size="14" /><input v-model="query" placeholder="搜索账号" aria-label="搜索平台账号"></label></div></div>
      <div class="platform-records">
        <table v-if="filtered.length" class="platform-account-table">
          <thead><tr><th scope="col">账号</th><th scope="col">授权状态</th><th scope="col">最近授权</th><th scope="col">操作</th></tr></thead>
          <tbody><tr v-for="account in filtered" :key="account.id" :data-account-id="account.id" :class="{ selected: account.id === selectedAccountId }">
            <td><div class="platform-record-name"><AccountAvatar :account="account" /><strong>{{ account.name }}</strong></div></td>
            <td><span class="platform-auth-status" :class="account.status"><LoaderCircle v-if="authorizing(account)" class="spinning" :size="13" /><span v-else class="account-dot" :class="account.status"></span>{{ label(account.status) }}</span><p v-if="account.last_login_error" class="platform-auth-error">{{ account.last_login_error }}</p></td>
            <td><time :datetime="account.last_login_at || undefined">{{ date(account.last_login_at) }}</time></td>
            <td><div class="platform-record-actions">
              <button v-if="account.status !== 'ready'" class="button light" type="button" :disabled="isBusy(account.id) || authorizing(account)" @click="emit('authorize', account)"><LogIn :size="14" />{{ authorizing(account) ? '授权中' : '授权' }}</button>
              <button v-if="authorizing(account) && account.platform !== 'chaoxing'" class="button dark" type="button" @click="emit('confirm-authorization',account)"><Check :size="14" />已完成登录</button>
              <template v-else-if="account.status === 'ready'"><button v-if="supportsBusiness(account, 'assignments')" class="button light" type="button" @click="emit('assignments', account.id)"><ClipboardList :size="14" />作业<ArrowUpRight :size="12" /></button><button v-if="supportsBusiness(account, 'learning')" class="button ghost" type="button" @click="emit('learning', account.id)"><BookOpen :size="14" />网课</button></template>
              <div class="account-menu-wrap"><button class="account-more icon-button" type="button" :aria-label="`${account.name} 账号操作`" title="账号操作" aria-haspopup="menu" :aria-expanded="menuId === account.id" @click.stop="emit('menu', account.id)"><Ellipsis :size="17" /></button>
                <Transition name="menu"><div v-if="menuId === account.id" class="account-menu" role="menu" :aria-label="`${account.name} 账号操作`" @keydown="emit('menu-keydown', $event)">

                  <button role="menuitem" type="button" :disabled="isBusy(account.id) || authorizing(account)" @click.stop="emit('authorize', account)"><LogIn :size="14" />重新授权</button>

                  <button role="menuitem" type="button" class="danger" :disabled="isBusy(account.id) || authorizing(account)" @click.stop="emit('delete', account)"><Trash2 :size="14" />删除</button>
                </div></Transition>
              </div>
            </div></td>
          </tr></tbody>
        </table>
        <div v-else class="empty-state"><Layers3 :size="30" /><h3>{{ query || filter !== 'all' ? '没有匹配的账号' : '暂无授权账号' }}</h3><button v-if="query || filter !== 'all'" class="button light" type="button" @click="query = ''; filter = 'all'">清除筛选</button><button v-else class="button dark" type="button" @click="emit('add', modelValue)"><LogIn :size="14" />授权</button></div>
      </div>
    </section>
  </main>
</template>

<style scoped>
.platform-management { display: grid; grid-column: 2; grid-row: 2; grid-template-columns: 220px minmax(0,1fr); min-width: 0; min-height: 0; background: var(--surface); }
.platform-catalog { border-right: 1px solid var(--line); background: var(--bg); padding: 25px 16px; overflow-y: auto; }
.platform-catalog h2 { font-size: 13px; }
.platform-catalog .platform-chip { margin-top: 18px; width: 100%; }
.platform-accounts { display: flex; flex-direction: column; min-height: 0; min-width: 0; padding: 28px 32px; }
.platform-accounts-heading { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding-bottom: 25px; border-bottom: 1px solid var(--line); }
.platform-accounts-heading > div { display: flex; align-items: center; gap: 12px; }
.platform-accounts-heading h1 { font-size: 20px; }
.platform-account-tools { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 16px; padding: 24px 0 18px; }
.platform-account-tools h2 { display: flex; align-items: center; gap: 9px; font-size: 14px; }
.platform-account-tools > div { display: flex; align-items: center; gap: 16px; }
.platform-account-tools .search-field { width: 200px; }
.platform-records { flex: 1; min-height: 0; overflow: auto; padding-bottom: 150px; }
.platform-account-table { width: 100%; border-collapse: collapse; table-layout: fixed; text-align: left; }
.platform-account-table th { font-size: 11px; font-weight: 500; color: var(--muted); padding: 12px 14px; background: var(--bg); border-bottom: 1px solid var(--line); }
.platform-account-table td { padding: 20px 14px; border-bottom: 1px solid var(--line); font-size: 12px; vertical-align: middle; }
.platform-account-table th:first-child { width: 24%; }
.platform-account-table th:nth-child(2) { width: 23%; }
.platform-account-table th:nth-child(3) { width: 19%; }
.platform-account-table th:last-child { width: 34%; }
.platform-account-table tr { transition: background 150ms ease; }
.platform-account-table tbody tr:hover { background: var(--bg); }
.platform-record-name { display: flex; align-items: center; gap: 10px; min-width: 0; }
.platform-record-name strong { overflow-wrap: anywhere; font-size: 13px; font-weight: 600; }
.platform-record-name .account-avatar { flex: none; }
.selected .platform-record-name .account-avatar { background: var(--accent-soft); color: var(--accent); }
.platform-auth-status { display: inline-flex; align-items: center; gap: 6px; color: var(--muted); white-space: nowrap; }
.platform-auth-status.ready { color: var(--green); }
.platform-auth-status.login_error { color: var(--red); }
.platform-auth-status.authorizing,.platform-auth-status.waiting { color: var(--orange); }
.platform-auth-error { max-width: 230px; margin-top: 8px; color: var(--red); font-size: 11px; line-height: 1.6; overflow-wrap: anywhere; }
.platform-account-table time { color: var(--muted); font-variant-numeric: tabular-nums; }
.platform-record-actions { display: flex; align-items: center; justify-content: flex-end; gap: 7px; }
.platform-record-actions .button { font-size: 11px; min-height: 30px; padding: 5px 8px; }
.platform-record-actions .account-menu-wrap { position: relative; top: auto; right: auto; }
.platform-record-actions .account-more { position: static; flex: none; }
.platform-account-table th:last-child { text-align: right; }
@media(max-width:1200px) { .platform-management { grid-template-columns: 190px minmax(0,1fr); } .platform-accounts { padding: 24px; } .platform-record-actions { gap: 3px; } .platform-record-actions .button { padding: 5px; } .platform-account-table td { padding: 18px 9px; } }
</style>
