<script setup>
import { computed } from 'vue';
import { Image, LogIn, MoreHorizontal, Users } from '@lucide/vue';
import AccountAvatar from '../../accounts/components/AccountAvatar.vue';
import PlatformLogo from '../../platforms/components/PlatformLogo.vue';

const props = defineProps({
  accounts: { type: Array, default: () => [] },
  notes: { type: Object, default: () => ({}) },
  qrGroups: { type: Array, default: () => [] },
  autoGroups: { type: Array, default: () => [] },
  profiles: { type: Object, default: () => ({}) },
});
const emit = defineEmits(['configure-assets', 'authorize']);

const readyCount = computed(() => props.accounts.filter(account => account.status === 'ready').length);
const groupCount = accountId => (
  props.qrGroups.filter(group => !group.revoked && group.account_ids.includes(accountId)).length
  + props.autoGroups.filter(group => group.account_ids.includes(accountId)).length
);
</script>

<template>
  <aside class="attendance-rail">
    <header class="attendance-rail-heading">
      <PlatformLogo platform="chaoxing" :size="38" />
      <span><h2>学习通账号</h2><p>{{ readyCount }} 个账号已授权</p></span>
    </header>

    <div class="attendance-account-list">
      <article v-for="account in accounts" :key="account.id" class="attendance-profile-row">
        <AccountAvatar :account="account" :size="32" />
        <div class="attendance-account-copy">
          <strong>{{ account.name }}</strong>
          <small>{{ notes[account.id] || (account.status === 'ready' ? '已授权' : '待授权') }}</small>
          <div class="attendance-profile-badges">
            <span>{{ groupCount(account.id) }} 个分组</span>
            <span v-if="profiles[account.id]?.photo"><Image :size="11" />拍照素材</span>
            <span v-if="profiles[account.id]?.face"><Users :size="11" />人脸校验</span>
          </div>
        </div>
        <button class="icon-button" type="button" title="配置账号签到素材" :aria-label="'配置 ' + account.name + ' 的拍照签到和人脸校验素材'" @click="emit('configure-assets', account)">
          <MoreHorizontal :size="17" />
        </button>
      </article>
      <div v-if="!accounts.length" class="attendance-rail-empty">
        <Users :size="24" />
        <strong>还没有学习通账号</strong>
        <span>授权账号后可创建扫码链接和自动签到分组。</span>
        <button class="button light" type="button" @click="emit('authorize')"><LogIn :size="14" />授权账号</button>
      </div>
    </div>

    <footer class="attendance-rail-footer">
      <span><i class="attendance-online-dot"></i>{{ readyCount }} 个可用</span>
      <span>{{ qrGroups.filter(group => !group.revoked).length }} 个链接分组</span>
    </footer>
  </aside>
</template>
