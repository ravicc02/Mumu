<script setup>
import { computed, onMounted, ref } from "vue";
import { ArrowDown, ArrowUp, Check, Download, Eye, EyeOff, LoaderCircle, Plus, Save, Trash2, Zap } from "@lucide/vue";
import { api } from "../../../app/api.js";

const emit = defineEmits(["saved"]);
const reasoningOptions = [
  { value: "none", label: "无" },
  { value: "minimal", label: "最少" },
  { value: "low", label: "低" },
  { value: "medium", label: "中" },
  { value: "high", label: "高" },
  { value: "xhigh", label: "极高" },
];
const form = ref({ provider_name: "", base_url: "", api_key: "", models: [], reasoning_effort: "high" });
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
const valid = computed(() => Boolean(form.value.provider_name.trim()) && /^https?:\/\//.test(form.value.base_url.trim()) && form.value.models.length > 0 && (keyConfigured.value || form.value.api_key.trim()));
const fetchReady = computed(() => /^https?:\/\//.test(form.value.base_url.trim()) && (keyConfigured.value || Boolean(form.value.api_key.trim())));
onMounted(load);

async function load() {
  loading.value = true; loadFailed.value = false; failed.value = false; message.value = "";
  try {
    const config = await api.modelConfig();
    form.value = {
      provider_name: config.provider_name === "custom" ? "" : config.provider_name || "",
      base_url: config.base_url || "",
      api_key: "",
      models: [...config.models || []],
      reasoning_effort: config.reasoning_effort || "high",
    };
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
  <form class="model-configuration settings-section-stack" @submit.prevent="perform('save')" :aria-busy="loading || Boolean(pending)">
    <div v-if="loading" class="settings-loading" role="status">
      <LoaderCircle class="spinning" :size="18" />正在读取模型配置
    </div>
    <div v-else-if="loadFailed" class="settings-load-error" role="alert">
      {{ message }}
      <button class="button light" type="button" @click="load">重试</button>
    </div>
    <template v-else>
      <section class="settings-card">
        <header class="settings-card-heading">
          <span class="settings-card-index">01</span>
          <div class="settings-card-title">
            <h3>服务连接</h3>
          </div>
          <div class="settings-card-heading-actions">
            <span class="settings-state" :class="{ ready: keyConfigured }">{{ keyConfigured ? '凭据已保存' : '等待配置' }}</span>
          </div>
        </header>
        <div class="settings-card-body padded">
          <div class="preference-form-grid model-form-grid">
            <label class="field-label settings-field">
              <span>供应商名称</span>
              <input v-model="form.provider_name" autocomplete="off" required placeholder="your-provider" :disabled="Boolean(pending)">
            </label>
            <div class="field-label reasoning-field">
              <span>思考强度</span>
              <div class="reasoning-options" role="group" aria-label="思考强度">
                <button
                  v-for="option in reasoningOptions"
                  :key="option.value"
                  type="button"
                  :class="{ active: form.reasoning_effort === option.value }"
                  :aria-pressed="form.reasoning_effort === option.value"
                  :disabled="Boolean(pending)"
                  @click="form.reasoning_effort = option.value"
                >
                  {{ option.label }}
                </button>
              </div>
            </div>
            <label class="field-label settings-field full">
              <span>接口地址</span>
              <input v-model="form.base_url" type="url" inputmode="url" autocomplete="off" placeholder="https://your-provider.example/v1" required :disabled="Boolean(pending)">
            </label>
            <label class="field-label settings-field full">
              <span>API Key</span>
              <span class="secret-field">
                <input
                  v-model="form.api_key"
                  aria-label="API Key"
                  :type="revealed ? 'text' : 'password'"
                  autocomplete="new-password"
                  :placeholder="keyConfigured ? '已配置，留空保留原密钥' : '输入 API Key'"
                  :disabled="Boolean(pending)"
                >
                <button class="icon-button" type="button" :aria-label="revealed ? '隐藏密钥' : '显示密钥'" :title="revealed ? '隐藏密钥' : '显示密钥'" @click="revealed = !revealed">
                  <component :is="revealed ? EyeOff : Eye" :size="15" />
                </button>
              </span>
            </label>
          </div>
        </div>
      </section>

      <section class="settings-card">
        <header class="settings-card-heading">
          <span class="settings-card-index">02</span>
          <div class="settings-card-title">
            <h3>模型顺序</h3>
          </div>
          <div class="settings-card-heading-actions">
            <span class="settings-scope">{{ form.models.length }} 个模型</span>
            <button class="button light compact" type="button" :disabled="Boolean(pending) || !fetchReady" :title="fetchReady ? '从模型服务获取可用模型' : '请先填写接口地址和 API Key'" @click="perform('fetch')">
              <LoaderCircle v-if="pending === 'fetch'" class="spinning" :size="14" />
              <Download v-else :size="14" />获取模型
            </button>
          </div>
        </header>
        <div class="settings-card-body">
          <ol v-if="form.models.length" class="preference-model-list">
            <li v-for="(model, index) in form.models" :key="model">
              <span class="model-order">{{ String(index + 1).padStart(2, '0') }}</span>
              <div class="model-name">
                <strong>{{ model }}</strong>
                <span class="model-priority-label" :class="{ primary: index === 0 }">{{ index === 0 ? '主模型' : '备用模型' }}</span>
              </div>
              <div class="model-order-actions">
                <button class="icon-button" type="button" :disabled="index === 0 || Boolean(pending)" aria-label="上移模型" title="上移" @click="move(index, -1)"><ArrowUp :size="14" /></button>
                <button class="icon-button" type="button" :disabled="index === form.models.length - 1 || Boolean(pending)" aria-label="下移模型" title="下移" @click="move(index, 1)"><ArrowDown :size="14" /></button>
                <button class="icon-button" type="button" :disabled="Boolean(pending)" :aria-label="'移除 ' + model" title="移除" @click="form.models.splice(index, 1)"><Trash2 :size="14" /></button>
              </div>
            </li>
          </ol>
          <p v-else class="model-empty">还没有模型。连接服务后获取模型，或手动填写模型名称。</p>
          <div class="model-add-panel">
            <div class="settings-inline-form">
              <input v-model="name" aria-label="模型名称" placeholder="your-model-name" autocomplete="off" :disabled="Boolean(pending)" @keydown.enter.prevent="add()">
              <button class="button light" type="button" :disabled="!name.trim() || Boolean(pending)" @click="add()"><Plus :size="14" />添加</button>
            </div>
            <select v-if="catalog.length" class="model-catalog" aria-label="添加可用模型" :disabled="Boolean(pending)" @change="add($event.target.value); $event.target.value = ''">
              <option value="">从已获取模型中选择</option>
              <option v-for="model in catalog" :key="model" :value="model">{{ model }}</option>
            </select>
          </div>
        </div>
      </section>

      <footer class="settings-action-bar preferences-save-bar">
        <span class="preferences-feedback" :class="{ error: failed }" role="status">
          <span v-if="failed || dirty" class="settings-feedback-dot" :class="{ dirty: dirty && !failed, error: failed }" aria-hidden="true" />
          <Check v-if="message && !failed && !pending" :size="14" />
          {{ message || (dirty ? '有未保存的更改' : '') }}
        </span>
        <button class="button light" type="button" :disabled="!valid || Boolean(pending)" @click="perform('test')">
          <LoaderCircle v-if="pending === 'test'" class="spinning" :size="14" />
          <Zap v-else :size="14" />测试连接
        </button>
        <button class="button dark" type="submit" :disabled="!valid || !dirty || Boolean(pending)">
          <LoaderCircle v-if="pending === 'save'" class="spinning" :size="14" />
          <Save v-else :size="14" />保存配置
        </button>
      </footer>
    </template>
  </form>
</template>

<style scoped>
.model-configuration {
  width: 100%;
}

.model-form-grid {
  grid-template-columns: minmax(0, 1fr) minmax(300px, .72fr);
}

.reasoning-field {
  align-content: start;
}

.reasoning-options {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  min-height: 40px;
  border: 1px solid var(--line);
  border-radius: 8px;
  overflow: hidden;
  background: #f3f5f7;
  padding: 3px;
  gap: 2px;
}

.reasoning-options button {
  min-width: 0;
  border: 0;
  border-radius: 5px;
  background: transparent;
  padding: 0 4px;
  color: var(--muted);
  font-size: 12px;
}

.reasoning-options button:hover:not(:disabled) {
  color: var(--ink);
  background: var(--surface);
  transition: color 140ms var(--ease), background 140ms var(--ease);
}

.reasoning-options button.active {
  background: var(--surface);
  color: var(--accent);
  box-shadow: inset 0 0 0 1px rgba(49, 87, 213, .28), 0 1px 2px rgba(20, 30, 42, .05);
}

.reasoning-options button:focus-visible {
  position: relative;
  z-index: 1;
  outline: 2px solid var(--accent);
  outline-offset: -2px;
}

.model-name {
  display: grid;
  gap: 2px;
  min-width: 0;
}

.model-name strong {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.model-priority-label.primary {
  color: var(--green);
}

.model-add-panel {
  display: grid;
  gap: 10px;
  border-top: 1px solid var(--line);
  margin-top: 6px;
  padding-top: 16px;
}

.model-empty {
  margin: 12px 0;
  border: 1px dashed rgba(24, 33, 44, .18);
  border-radius: 8px;
  background: #fafbfc;
  padding: 18px;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.6;
}

.model-catalog {
  margin-top: 0;
}

.model-configuration :deep(.preference-model-list li) {
  grid-template-columns: 34px minmax(0, 1fr) auto;
  min-height: 58px;
  gap: 12px;
}

.model-configuration :deep(.model-order) {
  color: var(--subtle);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}

@media (max-width: 720px) {
  .reasoning-options {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
  .reasoning-options button {
    min-height: 40px;
  }

  .model-form-grid {
    grid-template-columns: 1fr;
  }
}
</style>
