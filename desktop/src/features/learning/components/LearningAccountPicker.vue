<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref } from "vue";
import { Check, ChevronDown, Search } from "@lucide/vue";
import AccountAvatar from "../../accounts/components/AccountAvatar.vue";
import { platformById } from "../../platforms/lib/catalog.js";
import { filterLearningAccounts } from "../lib/accounts.js";

const props = defineProps({
  accounts: { type: Array, default: () => [] },
  modelValue: { type: String, default: "" },
});
const emit = defineEmits(["update:modelValue"]);
const root = ref(null);
const trigger = ref(null);
const search = ref(null);
const open = ref(false);
const query = ref("");
const current = computed(() => props.accounts.find(account => account.id === props.modelValue));
const filtered = computed(() => filterLearningAccounts(
  props.accounts,
  query.value,
  name => platformById[name]?.label,
));

async function show() {
  if (!props.accounts.length) return;
  open.value = true;
  query.value = "";
  await nextTick();
  search.value?.focus();
}

async function close(returnFocus = false) {
  open.value = false;
  query.value = "";
  if (returnFocus) {
    await nextTick();
    trigger.value?.focus();
  }
}

async function choose(account) {
  if (!account) return;
  emit("update:modelValue", account.id);
  await close(true);
}

function chooseFirst() {
  choose(filtered.value[0]);
}

function outside(event) {
  if (open.value && root.value && !root.value.contains(event.target)) close();
}

onMounted(() => document.addEventListener("pointerdown", outside));
onUnmounted(() => document.removeEventListener("pointerdown", outside));
</script>

<template>
  <div ref="root" class="learning-account-picker">
    <button
      ref="trigger"
      class="account-picker-trigger"
      type="button"
      :disabled="!accounts.length"
      :aria-expanded="open"
      aria-haspopup="listbox"
      aria-label="选择网课账号"
      @click="open ? close(true) : show()"
      @keydown.down.prevent="show"
    >
      <AccountAvatar v-if="current" :account="current" :size="28" />
      <span v-else class="account-picker-placeholder"><Search :size="14" /></span>
      <span class="account-picker-copy">
        <strong>{{ current?.name || (accounts.length ? "选择账号" : "暂无网课账号") }}</strong>
        <small v-if="current">{{ platformById[current.platform]?.label }} · {{ current.status === "ready" ? "已授权" : "待授权" }}</small>
        <small v-else>{{ accounts.length }} 个可选账号</small>
      </span>
      <ChevronDown :size="15" :class="{ rotated: open }" />
    </button>

    <Transition name="account-picker">
      <section v-if="open" class="account-picker-popover" role="dialog" aria-label="搜索并选择网课账号" @keydown.esc.stop.prevent="close(true)">
        <label class="account-picker-search">
          <Search :size="14" />
          <input
            ref="search"
            v-model="query"
            type="search"
            autocomplete="off"
            placeholder="搜索账号或平台"
            aria-label="搜索网课账号"
            @keydown.enter.prevent="chooseFirst"
          >
        </label>
        <div class="account-picker-list" role="listbox" aria-label="网课账号">
          <button
            v-for="account in filtered"
            :key="account.id"
            type="button"
            role="option"
            :aria-selected="account.id === modelValue"
            :class="{ selected: account.id === modelValue }"
            @click="choose(account)"
          >
            <AccountAvatar :account="account" :size="30" />
            <span><strong>{{ account.name }}</strong><small>{{ platformById[account.platform]?.label }} · {{ account.status === "ready" ? "已授权" : "待授权" }}</small></span>
            <Check v-if="account.id === modelValue" :size="15" />
          </button>
          <p v-if="!filtered.length" class="account-picker-empty">没有匹配的账号</p>
        </div>
      </section>
    </Transition>
  </div>
</template>

<style scoped>
.learning-account-picker {
  position: relative;
  width: 100%;
  min-width: 0;
}

.account-picker-trigger {
  display: grid;
  grid-template-columns: 30px minmax(0,1fr) auto;
  align-items: center;
  gap: 10px;
  width: 100%;
  min-height: 48px;
  border: 1px solid var(--line);
  border-radius: var(--radius-control);
  background: var(--surface);
  padding: 6px 10px;
  color: var(--ink);
  text-align: left;
}

.account-picker-trigger:hover:not(:disabled),
.account-picker-trigger[aria-expanded="true"] {
  border-color: var(--line-strong);
  background: var(--surface-muted);
}

.account-picker-trigger:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

.account-picker-trigger:disabled {
  color: var(--muted);
  cursor: not-allowed;
}

.account-picker-placeholder {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  border-radius: var(--radius-control);
  background: var(--surface-muted);
  color: var(--muted);
}

.account-picker-copy {
  min-width: 0;
}

.account-picker-copy strong,
.account-picker-copy small {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.account-picker-copy strong {
  font-size: var(--ds-text-base);
  line-height: 1.4;
}

.account-picker-copy small {
  margin-top: 2px;
  color: var(--muted);
  font-size: var(--text-xs);
}

.account-picker-trigger > svg {
  width: 16px;
  height: 16px;
  color: var(--muted);
  transition: transform var(--ds-motion-swift);
}

.account-picker-trigger > svg.rotated {
  transform: rotate(180deg);
}

.account-picker-popover {
  position: absolute;
  top: calc(100% + 8px);
  left: 0;
  z-index: var(--ds-z-floating);
  width: 100%;
  min-width: 240px;
  overflow: hidden;
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-card);
  background: var(--surface);
  box-shadow: var(--shadow-menu);
}

.account-picker-search {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 8px;
  height: 38px;
  border: 1px solid var(--line);
  border-radius: var(--radius-control);
  padding: 0 10px;
  color: var(--muted);
}

.account-picker-search:focus-within {
  border-color: var(--accent);
  box-shadow: var(--ds-input-shadow-focus);
}

.account-picker-search input {
  min-width: 0;
  flex: 1;
  border: 0;
  outline: 0;
  background: transparent;
  color: var(--ink);
  font-size: var(--text-sm);
}

.account-picker-search > svg {
  width: 16px;
  height: 16px;
  stroke-width: 1.5;
}

.account-picker-list {
  max-height: 270px;
  overflow-y: auto;
  padding: 0 6px 6px;
}

.account-picker-list > button {
  display: grid;
  grid-template-columns: 30px minmax(0,1fr) 16px;
  align-items: center;
  gap: 10px;
  width: 100%;
  min-height: 52px;
  border: 0;
  border-radius: var(--radius-control);
  background: transparent;
  padding: 10px;
  color: var(--ink);
  text-align: left;
}

.account-picker-list > button:hover,
.account-picker-list > button.selected {
  background: var(--accent-soft);
}

.account-picker-list > button:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: -2px;
}

.account-picker-list > button > span {
  min-width: 0;
}

.account-picker-list strong,
.account-picker-list small {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.account-picker-list strong {
  font-size: var(--ds-text-base);
}

.account-picker-list small {
  margin-top: 4px;
  color: var(--muted);
  font-size: var(--text-xs);
}

.account-picker-list > button > svg {
  width: 16px;
  height: 16px;
  stroke-width: 1.5;
  color: var(--accent);
}

.account-picker-empty {
  padding: 18px 10px;
  color: var(--muted);
  font-size: var(--text-sm);
  text-align: center;
}

.account-picker-enter-active,
.account-picker-leave-active {
  transition: opacity var(--ds-motion-swift), transform var(--ds-motion-swift);
}

.account-picker-enter-from,
.account-picker-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}

@media (prefers-reduced-motion: reduce) {
  .account-picker-trigger > svg,
  .account-picker-enter-active,
  .account-picker-leave-active {
    transition: none;
  }
}
</style>
