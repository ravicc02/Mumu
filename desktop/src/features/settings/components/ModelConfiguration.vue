<script setup>
import { computed, onMounted, ref } from "vue";
import { ArrowDown, ArrowUp, Check, Download, Eye, EyeOff, LoaderCircle, Plus, Save, Trash2, Zap } from "@lucide/vue";
import { api } from "../../../app/api.js";

const emit = defineEmits(["saved"]);
const form = ref({ provider_name: "custom", base_url: "", api_key: "", models: [], reasoning_effort: "high" });
const original = ref("");
const catalog = ref([]);
const name = ref("");
const keyConfigured = ref(false);
const revealed = ref(false);
const loading = ref(true);
const loadFailed = ref(false);
const pending = ref("");
const message = ref("");
const failed = ref(false);
const dirty = computed(() => JSON.stringify(form.value) !== original.value);
const valid = computed(() => /^https?:\/\//.test(form.value.base_url.trim()) && form.value.models.length > 0 && (keyConfigured.value || form.value.api_key.trim()));
onMounted(load);

async function load() {
  loading.value = true; loadFailed.value = false; failed.value = false; message.value = "";
  try {
    const config = await api.modelConfig();
    form.value = { provider_name: config.provider_name || "custom", base_url: config.base_url || "", api_key: "", models: [...config.models || []], reasoning_effort: config.reasoning_effort || "high" };
    keyConfigured.value = Boolean(config.api_key_configured || config.has_api_key || config.configured);
    original.value = JSON.stringify(form.value);
  } catch (error) { loadFailed.value = true; failed.value = true; message.value = error.message; }
  finally { loading.value = false; }
}
function add(value = name.value) {
  const model = value.trim();
  if (model && !form.value.models.includes(model)) { form.value.models.push(model); name.value = ""; }
}
function move(index, delta) {
  const models = [...form.value.models];
  [models[index], models[index + delta]] = [models[index + delta], models[index]];
  form.value.models = models;
}
async function perform(kind) {
  if (pending.value) return;
  pending.value = kind; message.value = ""; failed.value = false;
  try {
    if (kind === "fetch") {
      const result = await api.fetchModels(form.value);
      catalog.value = result.models || [];
      message.value = `已获取 ${catalog.value.length} 个模型`;
    } else if (kind === "test") { await api.testModel(form.value); message.value = "连接成功"; }
    else {
      await api.saveModelConfig(form.value);
      keyConfigured.value = true; form.value.api_key = "";
      original.value = JSON.stringify(form.value); message.value = "模型配置已保存";
      emit("saved");
    }
  } catch (error) { failed.value = true; message.value = error.message; }
  finally { pending.value = ""; }
}
</script>

<template>
  <form class="model-configuration" @submit.prevent="perform('save')" :aria-busy="loading || Boolean(pending)">
    <div v-if="loading" class="preferences-loading" role="status"><LoaderCircle class="spinning" :size="18" />正在读取模型配置</div>
    <div v-else-if="loadFailed" class="preferences-load-error" role="alert">{{ message }}<button class="button light" type="button" @click="load">重试</button></div>
    <template v-else>
      <div class="preference-form-grid">
        <label class="field-label">供应商名称<input id="model-provider-input" v-model="form.provider_name" autocomplete="off" required :disabled="Boolean(pending)"></label>
        <label class="field-label">思考强度<select v-model="form.reasoning_effort" :disabled="Boolean(pending)"><option value="none">无</option><option value="minimal">最少</option><option value="low">低</option><option value="medium">中</option><option value="high">高</option><option value="xhigh">极高</option></select></label>
      </div>
      <label class="field-label">接口地址<input v-model="form.base_url" type="url" placeholder="https://api.example.com/v1" required :disabled="Boolean(pending)"></label>
      <label class="field-label">API Key
        <span class="secret-field"><input v-model="form.api_key" aria-label="API Key" :type="revealed ? 'text' : 'password'" autocomplete="new-password" :placeholder="keyConfigured ? '已配置，留空保留原密钥' : '输入 API Key'" :disabled="Boolean(pending)"><button class="icon-button" type="button" :aria-label="revealed ? '隐藏密钥' : '显示密钥'" :title="revealed ? '隐藏密钥' : '显示密钥'" @click="revealed = !revealed"><component :is="revealed ? EyeOff : Eye" :size="15" /></button></span>
      </label>
      <div class="preference-section-heading model-heading"><h3>模型顺序 <span class="count">{{ form.models.length }}</span></h3><button class="button light compact" type="button" :disabled="Boolean(pending)" @click="perform('fetch')"><LoaderCircle v-if="pending === 'fetch'" class="spinning" :size="14" /><Download v-else :size="14" />获取模型</button></div>
      <ol class="preference-model-list">
        <li v-for="(model, index) in form.models" :key="model"><span class="model-order">{{ index + 1 }}</span><strong>{{ model }}</strong><span class="model-priority-label">{{ index === 0 ? '主模型' : '备用' }}</span><div class="model-order-actions"><button class="icon-button" type="button" :disabled="index === 0 || Boolean(pending)" aria-label="上移模型" title="上移" @click="move(index, -1)"><ArrowUp :size="14" /></button><button class="icon-button" type="button" :disabled="index === form.models.length - 1 || Boolean(pending)" aria-label="下移模型" title="下移" @click="move(index, 1)"><ArrowDown :size="14" /></button><button class="icon-button" type="button" :disabled="Boolean(pending)" :aria-label="'移除 ' + model" title="移除" @click="form.models.splice(index, 1)"><Trash2 :size="14" /></button></div></li>
      </ol>
      <div class="inline-form"><input v-model="name" aria-label="模型名称" placeholder="模型名称" :disabled="Boolean(pending)" @keydown.enter.prevent="add()"><button class="button light" type="button" :disabled="!name.trim() || Boolean(pending)" @click="add()"><Plus :size="14" />添加</button></div>
      <select v-if="catalog.length" class="model-catalog" aria-label="添加可用模型" :disabled="Boolean(pending)" @change="add($event.target.value); $event.target.value = ''"><option value="">选择模型</option><option v-for="model in catalog" :key="model" :value="model">{{ model }}</option></select>
      <div class="preferences-save-bar"><span class="preferences-feedback" :class="{ error: failed }" role="status"><Check v-if="message && !failed && !pending" :size="14" />{{ message || (dirty ? '有未保存的更改' : '') }}</span><button class="button light" type="button" :disabled="!valid || Boolean(pending)" @click="perform('test')"><LoaderCircle v-if="pending === 'test'" class="spinning" :size="14" /><Zap v-else :size="14" />测试连接</button><button class="button dark" type="submit" :disabled="!valid || !dirty || Boolean(pending)"><LoaderCircle v-if="pending === 'save'" class="spinning" :size="14" /><Save v-else :size="14" />保存配置</button></div>
    </template>
  </form>
</template>
