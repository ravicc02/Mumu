<script setup>
import { onMounted, ref, computed } from 'vue';
import { api } from '../../../app/api.js';
const options = ['none', 'minimal', 'low', 'medium', 'high', 'xhigh'];
const labels = ['无', '最少', '低', '中', '高', '极高'];
const index = ref(4), saved = ref(4), loading = ref(true), pending = ref(false), error = ref(''), message = ref('');
const label = computed(() => labels[index.value]);
async function load() {
  loading.value = true; error.value = '';
  try { const config = await api.modelConfig(); const value = options.indexOf(config.reasoning_effort); index.value = saved.value = value < 0 ? 4 : value; }
  catch (e) { error.value = e.message; }
  finally { loading.value = false; }
}
async function save() {
  if (pending.value || index.value === saved.value) return;
  pending.value = true; error.value = ''; message.value = '';
  try { await api.saveReasoningEffort(options[index.value]); saved.value = index.value; message.value = '已保存'; }
  catch (e) { index.value = saved.value; error.value = e.message === 'Not Found' ? '请在任务结束后重启应用，加载思考强度设置接口' : e.message; }
  finally { pending.value = false; }
}
onMounted(load);
</script>
<template>
  <section class="reasoning-slider">
    <header><label for="mumu-reasoning">思考强度</label><output for="mumu-reasoning">{{ label }}</output></header>
    <input id="mumu-reasoning" v-model.number="index" type="range" min="0" max="5" step="1" :disabled="loading || pending || Boolean(error)" :aria-valuetext="label" @input="message = ''" @change="save">
    <div class="reasoning-scale" aria-hidden="true"><span>无</span><span>极高</span></div>
    <p v-if="error" role="alert">{{ error }} <button class="text-button" type="button" @click="load">重试</button></p>
    <span v-else class="reasoning-feedback" role="status">{{ loading ? '正在读取' : pending ? '保存中' : message }}</span>
  </section>
</template>
<style scoped>
.reasoning-slider{padding:16px 20px;border-bottom:1px solid var(--line);}
header{display:flex;justify-content:space-between;font-size:13px;margin-bottom:12px;}output{color:var(--accent);}
input{display:block;width:100%;height:20px;accent-color:var(--accent);padding:0;cursor:ew-resize;}input:disabled{cursor:default;opacity:.5;}
.reasoning-scale{display:flex;justify-content:space-between;color:var(--muted);font-size:11px;margin-top:4px;}
p{font-size:12px;color:var(--red);overflow-wrap:anywhere;}.reasoning-feedback{display:block;min-height:16px;color:var(--muted);font-size:11px;margin-top:4px;}
</style>
